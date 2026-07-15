from __future__ import annotations

from pydantic import BaseModel, Field

from .contracts import Claim


class WorkingMemory(BaseModel):
    goal: str = ""
    known_facts: list[str] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    observations: list[dict] = Field(default_factory=list)
    next_objective: str = ""
    completion_ready: bool = False
    completion_reason: str = ""
