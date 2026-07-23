"""Registry that constructs V2 descriptors from manifests and legacy implementations."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from skills import SkillLoader

from .contracts import SkillManifest, ToolDescriptor
from .errors import ManifestValidationError, PolicyViolationError
from .manifest_loader import load_manifest
from .policy import profile_allows_tool


class SharedSkillRegistry:
    def __init__(self, workspace_path: str | Path | None = None) -> None:
        self.workspace_path = Path(workspace_path or Path(__file__).resolve().parents[3])
        self.skills_dir = self.workspace_path / "src" / "skills"
        self._manifests: dict[str, SkillManifest] = {}

    def load_manifests(self, skill_ids: Iterable[str] = ("knowledge_retrieval", "hifleet_data", "ship_info_update")) -> dict[str, SkillManifest]:
        manifests: dict[str, SkillManifest] = {}
        for skill_id in skill_ids:
            manifest = load_manifest(self.skills_dir / skill_id / "manifest.yaml")
            if manifest.schema_version != 1 or manifest.skill_id != skill_id:
                raise ManifestValidationError(f"Unsupported manifest for {skill_id}")
            manifests[skill_id] = manifest
        self._manifests = manifests
        return manifests

    @staticmethod
    def _schema_for(tool: Any) -> dict[str, Any]:
        schema_model = getattr(tool, "args_schema", None)
        if schema_model is None:
            raise ManifestValidationError(f"Tool {getattr(tool, 'name', '<unknown>')} has no input schema")
        if hasattr(schema_model, "model_json_schema"):
            schema = schema_model.model_json_schema()
        elif hasattr(schema_model, "schema"):
            schema = schema_model.schema()
        else:
            raise ManifestValidationError(f"Tool {getattr(tool, 'name', '<unknown>')} schema is unsupported")
        schema.pop("title", None)
        return schema

    def descriptors_for(self, skill_ids: Iterable[str], *, external_profile: bool = True) -> tuple[ToolDescriptor, ...]:
        manifests = self.load_manifests(skill_ids)
        requested_names = [
            str(capability.get("tool_name") or capability.get("id"))
            for manifest in manifests.values()
            for capability in manifest.capabilities
            if str(capability.get("tool_name") or capability.get("id")) not in {"prepare_ship_update", "commit_ship_update", "cancel_ship_update"}
        ]
        tools_by_name = {tool.name: tool for tool in SkillLoader.get_tools_by_names(requested_names)}
        descriptors: list[ToolDescriptor] = []
        seen: set[str] = set()
        for skill_id, manifest in manifests.items():
            for capability in manifest.capabilities:
                name = str(capability.get("tool_name") or capability.get("id"))
                if name in seen:
                    raise ManifestValidationError(f"Duplicate V2 tool name: {name}")
                seen.add(name)
                if external_profile and not profile_allows_tool(skill_id, name):
                    raise PolicyViolationError(f"Tool {name} is not allowed for external V2 profiles")
                if name in {"prepare_ship_update", "commit_ship_update", "cancel_ship_update"}:
                    schema = dict(capability["input_schema"])
                else:
                    tool = tools_by_name.get(name)
                    if tool is None:
                        raise ManifestValidationError(f"Manifest tool {name} is not available in legacy implementation")
                    schema = self._schema_for(tool)
                descriptors.append(ToolDescriptor(
                    name=name,
                    skill_id=skill_id,
                    description=str(capability.get("description") or ""),
                    input_schema=schema,
                    read_only=bool(capability.get("read_only", True)),
                    risk_level=str(capability.get("risk_level", "low")),
                    timeout_seconds=int(capability.get("timeout_seconds", 20)),
                    requires_confirmation=bool(capability.get("requires_confirmation", False)),
                    upstream_commit=manifest.upstream_commit,
                    skill_version=manifest.skill_version,
                    metadata={"capability_id": str(capability.get("id") or name)},
                ))
        return tuple(descriptors)

    def tools_for(self, descriptors: Iterable[ToolDescriptor]) -> tuple[Any, ...]:
        wanted = [descriptor.name for descriptor in descriptors if descriptor.name not in {"prepare_ship_update", "commit_ship_update", "cancel_ship_update"}]
        return tuple(SkillLoader.get_tools_by_names(wanted))

    def prompt_for(self, skill_ids: Iterable[str]) -> str:
        manifests = self._manifests or self.load_manifests(skill_ids)
        parts: list[str] = []
        for skill_id in skill_ids:
            manifest = manifests[skill_id]
            prompt_path = self.skills_dir / skill_id / manifest.prompt_file
            if not prompt_path.exists():
                raise ManifestValidationError(f"Prompt file is missing for {skill_id}")
            parts.append(prompt_path.read_text(encoding="utf-8").strip())
        return "\n\n---\n\n".join(part for part in parts if part)
