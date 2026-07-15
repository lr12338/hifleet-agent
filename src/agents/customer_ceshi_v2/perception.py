from __future__ import annotations

import hashlib
import json
from typing import Any

from .contracts import InspectMediaRequest, MediaAsset, Observation, PerceptionPacket


class PerceptionCache:
    def __init__(self):
        self._items: dict[str, PerceptionPacket] = {}

    def key(self, asset: MediaAsset, request: InspectMediaRequest, model: str) -> str:
        payload = {"asset": asset.sha256 or asset.url, "model": model, "schema": "1.0", "mode": request.mode, "objective": request.objective, "questions": request.questions, "region": request.region, "time_range": request.time_range}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def get(self, asset: MediaAsset, request: InspectMediaRequest, model: str) -> PerceptionPacket | None:
        return self._items.get(self.key(asset, request, model))

    def put(self, asset: MediaAsset, request: InspectMediaRequest, model: str, packet: PerceptionPacket) -> None:
        self._items[self.key(asset, request, model)] = packet


class PerceptionService:
    def __init__(self, client: Any, cache: PerceptionCache | None = None):
        self.client = client
        self.cache = cache or PerceptionCache()
        self.last_call_count = 0

    def inspect(self, assets: list[MediaAsset], requests: list[InspectMediaRequest]) -> tuple[list[Observation], int]:
        observations: list[Observation] = []
        cache_hits = 0
        self.last_call_count = 0
        by_id = {asset.asset_id: asset for asset in assets}
        for request in requests:
            request_calls = 0
            asset = by_id.get(request.asset_id)
            if asset is None:
                observations.append(Observation(status="invalid_input", capability="inspect_media", warnings=[f"Unknown asset_id: {request.asset_id}"], retry_allowed=False))
                continue
            cached = self.cache.get(asset, request, getattr(self.client, "model", "multimodal"))
            if cached:
                cache_hits += 1
                observations.append(self._observation(cached, asset=asset, cached=True, perception_passes=0))
                continue
            self.last_call_count += 1
            request_calls += 1
            packet = self.client.inspect_packet(asset, request)
            if isinstance(packet, PerceptionPacket):
                if self._has_visual_evidence(packet):
                    self.cache.put(asset, request, getattr(self.client, "model", "multimodal"), packet)
                    observations.append(self._observation(packet, asset=asset, cached=False, perception_passes=1))
                    continue
                if asset.kind == "image":
                    detail_request = request.model_copy(
                        update={
                            "mode": "targeted_verify",
                            "objective": f"Inspect image details after an empty first pass. {request.objective}",
                        }
                    )
                    detail_asset = asset.model_copy(update={"metadata": {**dict(asset.metadata or {}), "perception_detail": True}})
                    self.last_call_count += 1
                    request_calls += 1
                    detail_packet = self.client.inspect_packet(detail_asset, detail_request)
                    if isinstance(detail_packet, PerceptionPacket) and self._has_visual_evidence(detail_packet):
                        self.cache.put(asset, request, getattr(self.client, "model", "multimodal"), detail_packet)
                        observations.append(self._observation(detail_packet, asset=detail_asset, cached=False, perception_passes=2))
                        continue
                    if isinstance(detail_packet, Observation):
                        local_packet = self._local_fallback_packet(asset, request)
                        if local_packet is not None:
                            self.cache.put(asset, request, getattr(self.client, "model", "multimodal"), local_packet)
                            observations.append(self._observation(local_packet, asset=asset, cached=False, perception_passes=request_calls))
                            continue
                        observations.append(self._with_pass_count(detail_packet, asset.asset_id, perception_passes=2))
                        continue
                local_packet = self._local_fallback_packet(asset, request)
                if local_packet is not None:
                    self.cache.put(asset, request, getattr(self.client, "model", "multimodal"), local_packet)
                    observations.append(self._observation(local_packet, asset=asset, cached=False, perception_passes=request_calls))
                    continue
                observations.append(self._no_visual_evidence(packet, asset=asset, perception_passes=request_calls))
            elif isinstance(packet, Observation):
                local_packet = self._local_fallback_packet(asset, request) if asset.kind == "image" else None
                if local_packet is not None:
                    self.cache.put(asset, request, getattr(self.client, "model", "multimodal"), local_packet)
                    observations.append(self._observation(local_packet, asset=asset, cached=False, perception_passes=request_calls))
                    continue
                observations.append(self._with_pass_count(packet, asset.asset_id, perception_passes=1))
            else:
                observations.append(Observation(status="temporary_error", capability="inspect_media", warnings=["Perception returned an invalid packet."], retry_allowed=False))
        return observations, cache_hits

    @staticmethod
    def _has_visual_evidence(packet: PerceptionPacket) -> bool:
        return bool(
            packet.factual_summary.strip()
            or packet.visual_features
            or packet.suspected_symbol.strip()
            or packet.transcript
            or packet.ocr_blocks
            or packet.visual_objects
            or packet.entities
            or packet.fields
            or packet.events
        )

    @staticmethod
    def _observation(packet: PerceptionPacket, *, asset: MediaAsset, cached: bool, perception_passes: int) -> Observation:
        facts = [packet.factual_summary] if packet.factual_summary else []
        facts.extend(packet.visual_features)
        if packet.suspected_symbol:
            facts.append(f"Suspected chart symbol: {packet.suspected_symbol}")
        facts.extend(field.raw_text or str(field.value) for field in packet.fields if field.status == "observed" and (field.raw_text or field.value is not None))
        return Observation(
            status="success",
            capability="inspect_media",
            facts=facts,
            data={
                "perception_packet": packet.model_dump(),
                "cached": cached,
                "asset_id": asset.asset_id,
                "media_diagnostics": dict((asset.metadata or {}).get("media_input") or {}),
                "media_perception_passes": perception_passes,
            },
            warnings=packet.limitations,
            retry_allowed=False,
        )

    @staticmethod
    def _with_pass_count(observation: Observation, asset_id: str, *, perception_passes: int) -> Observation:
        return observation.model_copy(
            update={
                "data": {
                    **dict(observation.data or {}),
                    "asset_id": str((observation.data or {}).get("asset_id") or asset_id),
                    "media_perception_passes": perception_passes,
                }
            }
        )

    @staticmethod
    def _no_visual_evidence(packet: PerceptionPacket, *, asset: MediaAsset, perception_passes: int) -> Observation:
        return Observation(
            status="not_found",
            capability="inspect_media",
            data={
                "asset_id": asset.asset_id,
                "perception_packet": packet.model_dump(),
                "media_diagnostics": dict((asset.metadata or {}).get("media_input") or {}),
                "media_perception_passes": perception_passes,
            },
            warnings=["media_no_visual_evidence"],
            retry_allowed=True,
            suggested_fix="请重新上传更清晰、包含完整图例或标记区域的截图。",
        )

    @staticmethod
    def _local_fallback_packet(asset: MediaAsset, request: InspectMediaRequest) -> PerceptionPacket | None:
        hint = dict((asset.metadata or {}).get("media_input", {}).get("media_local_hint") or {})
        if not hint:
            return None
        return PerceptionPacket(
            asset_id=asset.asset_id,
            media_type=asset.kind,
            model="local_visual_fallback",
            requested_objective=request.objective,
            requested_questions=request.questions,
            factual_summary=str(hint.get("summary") or ""),
            visual_features=[str(item) for item in hint.get("visual_features", []) if item],
            suspected_symbol=str(hint.get("suspected_symbol") or ""),
            overall_confidence=str(hint.get("confidence") or "low"),
            limitations=["该结论由本地可见特征初步判断，建议结合平台图例或原始海图复核。"],
            evidence_refs=[{"type": "local_visual_feature_fallback"}],
        )
