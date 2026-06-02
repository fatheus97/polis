"""The Registry — role *templates* vs live *instances*.

A template is a durable job description (how to build an agent of some role); an
instance is a running official. "Hiring" = ``spawn`` (instantiate a template);
"releasing" = ``release`` (drop the instance, keep the template). Phase 0 ships the
three core roles; Phase 2 adds specialist dev templates here with no other change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .agents.base import Agent
from .agents.stubs import StubArchitect, StubDev, StubReviewer
from .models import Branch, gen_id


@dataclass
class RoleTemplate:
    name: str
    branch: Branch
    factory: Callable[[], Agent]
    description: str = ""


@dataclass
class ModelTier:
    """Cost-tiered model assignment: strong models judge & spec, a cheap one grinds."""

    architect: str = "sonnet"
    reviewer: str = "sonnet"
    dev: str = "haiku"


class Registry:
    def __init__(self):
        self._templates: dict[str, RoleTemplate] = {}
        self._instances: dict[str, Agent] = {}

    def register(self, template: RoleTemplate) -> None:
        self._templates[template.name] = template

    def has(self, name: str) -> bool:
        return name in self._templates

    def template(self, name: str) -> RoleTemplate:
        return self._templates[name]

    def spawn(self, name: str) -> Agent:
        if name not in self._templates:
            raise KeyError(f"no role template registered: {name!r}")
        agent = self._templates[name].factory()
        agent.instance_id = gen_id("inst")
        self._instances[agent.instance_id] = agent
        return agent

    def release(self, agent: Agent) -> None:
        if agent.instance_id:
            self._instances.pop(agent.instance_id, None)

    def live_instances(self) -> list[Agent]:
        return list(self._instances.values())

    @classmethod
    def default(cls) -> "Registry":
        """The Phase-0 government: deterministic stub officials (no LLM)."""
        reg = cls()
        reg.register(RoleTemplate("architect", Branch.LEGISLATIVE, StubArchitect,
                                  "Writes PRDs from feedback."))
        reg.register(RoleTemplate("dev", Branch.EXECUTIVE, StubDev,
                                  "Implements PRDs into diffs."))
        reg.register(RoleTemplate("reviewer", Branch.JUDICIAL, StubReviewer,
                                  "Judges diffs against PRD + constitution."))
        return reg

    @classmethod
    def real(cls, backend, tier: "ModelTier | None" = None) -> "Registry":
        """The Phase-1 government: real, model-backed officials sharing one backend."""
        from .agents.llm_agents import ClaudeCodeDev, LLMArchitect, LLMReviewer
        tier = tier or ModelTier()
        reg = cls()
        reg.register(RoleTemplate("architect", Branch.LEGISLATIVE,
                                  lambda: LLMArchitect(backend, tier.architect),
                                  "LLM architect: feedback -> PRD."))
        reg.register(RoleTemplate("dev", Branch.EXECUTIVE,
                                  lambda: ClaudeCodeDev(backend, tier.dev),
                                  "Claude Code dev: PRD -> code edits."))
        reg.register(RoleTemplate("reviewer", Branch.JUDICIAL,
                                  lambda: LLMReviewer(backend, tier.reviewer),
                                  "LLM reviewer: diff -> verdict (+ hard gates)."))
        return reg
