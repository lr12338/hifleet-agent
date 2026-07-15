from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from typing import Any

from skills import SkillLoader

from .contracts import Observation, ToolCall


WRITE_TOOL_NAMES = {"upload_ship_position", "update_ship_static_info"}
DENIED_TOOL_NAMES = WRITE_TOOL_NAMES | {"upsert_local_kb_entry", "download_public_file_to_artifact", "run_sandboxed_python", "upload_customer_artifact"}


@dataclass(frozen=True)
class ToolDescriptor:
    name: str
    capability: str
    description: str = ""
    read_only: bool = True
    risk_level: str = "low"
    requires_confirmation: bool = False
    required_arguments: tuple[str, ...] = ()
    optional_arguments: tuple[str, ...] = ()
    timeout_seconds: int = 20
    cost_class: str = "standard"


class CapabilityRegistry:
    def __init__(self, tools: list[Any] | None = None, skill_names: list[str] | None = None):
        tools = tools if tools is not None else SkillLoader.get_tools_by_skill_names(skill_names or [])
        self._tools = {tool.name: tool for tool in tools if getattr(tool, "name", "") not in DENIED_TOOL_NAMES}

    def descriptors(self) -> list[ToolDescriptor]:
        descriptors: list[ToolDescriptor] = []
        for name, tool in self._tools.items():
            schema = getattr(tool, "args_schema", None)
            fields = getattr(schema, "model_fields", {}) or {}
            required = tuple(field_name for field_name, field in fields.items() if getattr(field, "is_required", lambda: False)())
            optional = tuple(field_name for field_name in fields if field_name not in required)
            descriptors.append(ToolDescriptor(name=name, capability=name, description=str(getattr(tool, "description", ""))[:500], required_arguments=required, optional_arguments=optional))
        return descriptors

    def has(self, name: str) -> bool:
        return name in self._tools

    def invoke(self, call: ToolCall) -> Observation:
        if call.name in DENIED_TOOL_NAMES or not self.has(call.name):
            return Observation(status="forbidden", capability=call.name, warnings=["Capability is not available to this agent."], retry_allowed=False)
        descriptor = next((item for item in self.descriptors() if item.name == call.name), None)
        missing = [name for name in (descriptor.required_arguments if descriptor else ()) if name not in call.arguments or call.arguments[name] in (None, "")]
        if missing:
            return Observation(status="invalid_input", capability=call.name, warnings=[f"Missing required arguments: {', '.join(missing)}"], retry_allowed=True, suggested_fix="Supply the missing arguments.")
        executor = ThreadPoolExecutor(max_workers=1)
        try:
            raw = executor.submit(self._tools[call.name].invoke, call.arguments).result(timeout=descriptor.timeout_seconds if descriptor else 20)
        except FutureTimeoutError:
            return Observation(status="timeout", capability=call.name, warnings=["Tool call timed out."], retry_allowed=True, suggested_fix="Retry once or use a narrower query.")
        except (TimeoutError, ConnectionError) as exc:
            return Observation(status="temporary_error", capability=call.name, warnings=[str(exc)[:240]], retry_allowed=True)
        except Exception as exc:  # Tool providers have heterogeneous exception types.
            return Observation(status="upstream_error", capability=call.name, warnings=[str(exc)[:240]], retry_allowed=False)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        result = self._normalize(call.name, raw)
        result.data.setdefault("information_gain", "new facts returned" if result.facts else "no new facts returned")
        return result

    @staticmethod
    def _normalize(capability: str, raw: Any) -> Observation:
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = {"text": raw}
        elif isinstance(raw, dict):
            parsed = raw
        else:
            parsed = {"value": str(raw)}
        status = str(parsed.get("status", "success")).lower()
        if isinstance(parsed.get("code"), int) and parsed["code"] >= 400:
            status = "upstream_error"
        elif str(parsed.get("result", "")).lower() == "failed":
            status = "not_found"
        if status not in {"success", "partial", "not_found", "invalid_input", "forbidden", "temporary_error", "timeout", "upstream_error"}:
            status = "success"
        sources = parsed.get("sources") or parsed.get("urls") or []
        if isinstance(sources, str):
            sources = [sources]
        facts = parsed.get("facts") or [parsed.get("text") or str(raw)]
        return Observation(status=status, capability=capability, facts=[str(item)[:2000] for item in facts if item], data=parsed if isinstance(parsed, dict) else {}, sources=[str(item) for item in sources])
