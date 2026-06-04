"""The Registry — role *templates* vs live *instances*.

A template is a durable job description (how to build an agent of some role); an
instance is a running official. "Hiring" = ``spawn`` (instantiate a template);
"releasing" = ``release`` (drop the instance, keep the template). Phase 0 ships the
three core roles; Phase 2 adds specialist dev templates here with no other change.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable

from .agents.base import Agent
from .agents.stubs import StubArchitect, StubConstitutionalJudge, StubDev, StubReviewer
from .models import Branch, gen_id


@dataclass
class RoleTemplate:
    name: str
    branch: Branch
    factory: Callable[[], Agent]
    description: str = ""


@dataclass
class ModelTier:
    """Per-role models. The dev writes real code, so the default is `sonnet` (Haiku proved too
    weak — it wrote hanging tests and non-portable `git checkout main`). Override per role via
    `config --architect-model/--dev-model/--review-model` (e.g. `--architect-model opus`)."""

    architect: str = "sonnet"
    reviewer: str = "sonnet"
    dev: str = "sonnet"


# Curated specialist expertise. An unknown discipline is still hireable — its
# expertise is synthesized from the name ("hire the expert if one doesn't exist").
SPECIALISTS = {
    "frontend": "frontend/UI engineering (HTML/CSS/JS/TS, components, accessibility, responsive layout)",
    "backend": "backend/server engineering (APIs, services, business logic, data flow)",
    "database": "database engineering (schema design, migrations, queries, indexing, integrity)",
    "infra": "infrastructure engineering (provisioning, configuration, networking, IaC)",
    "devops": "devops / CI-CD (pipelines, builds, deployment, automation)",
    "cli": "command-line tooling (argument parsing, UX, scripting)",
    "prompt": "LLM prompt engineering (prompt design, evaluation, structured output)",
}


class Registry:
    def __init__(self):
        self._templates: dict[str, RoleTemplate] = {}
        self._instances: dict[str, Agent] = {}
        self._lock = threading.Lock()  # guards the instance pool across parallel runs
        # Builds a dev for a given discipline (None => generalist). Set by
        # default()/real(); enables specialist hiring without a fixed template per role.
        self._dev_factory = None

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
        with self._lock:
            agent.instance_id = gen_id("inst")
            self._instances[agent.instance_id] = agent
        return agent

    def hire_dev(self, discipline: str | None = None) -> Agent:
        """Spawn a dev for a discipline (None => generalist). The 'hire a specialist'
        primitive: real registries synthesize an expert for any discipline name."""
        if self._dev_factory is not None:
            agent = self._dev_factory(discipline)
        else:
            agent = self._templates["dev"].factory()
        with self._lock:
            agent.instance_id = gen_id("inst")
            self._instances[agent.instance_id] = agent
        return agent

    def release(self, agent: Agent) -> None:
        with self._lock:
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
        reg.register(RoleTemplate("constitutional-judge", Branch.JUDICIAL,
                                  StubConstitutionalJudge, "Reviews PRDs for constitutionality."))
        reg._dev_factory = lambda discipline: StubDev(specialty=discipline)
        return reg

    @classmethod
    def real(cls, backend, tier: "ModelTier | None" = None,
             testing_mode: bool = False, dev_timeout: int = 900,
             grounded: bool = False) -> "Registry":
        """The Phase-1 government: real, model-backed officials sharing one backend."""
        from .agents.llm_agents import (ClaudeCodeDev, LLMArchitect,
                                        LLMConstitutionalJudge, LLMReviewer)
        tier = tier or ModelTier()
        reg = cls()
        reg.register(RoleTemplate("architect", Branch.LEGISLATIVE,
                                  lambda: LLMArchitect(backend, tier.architect,
                                                       testing_mode=testing_mode),
                                  "LLM architect: feedback -> PRD."))
        reg.register(RoleTemplate("dev", Branch.EXECUTIVE,
                                  lambda: ClaudeCodeDev(backend, tier.dev,
                                                        testing_mode=testing_mode,
                                                        timeout=dev_timeout),
                                  "Claude Code dev: PRD -> code edits."))
        reg.register(RoleTemplate("reviewer", Branch.JUDICIAL,
                                  lambda: LLMReviewer(backend, tier.reviewer, grounded=grounded),
                                  "LLM reviewer: diff -> verdict (+ hard gates)."))
        reg.register(RoleTemplate("constitutional-judge", Branch.JUDICIAL,
                                  lambda: LLMConstitutionalJudge(backend, tier.reviewer),
                                  "LLM court: reviews PRDs for constitutionality."))

        def _hire(discipline):
            if discipline:
                expertise = SPECIALISTS.get(discipline, f"{discipline} engineering")
                return ClaudeCodeDev(backend, tier.dev, specialty=expertise,
                                     testing_mode=testing_mode, timeout=dev_timeout)
            return ClaudeCodeDev(backend, tier.dev, testing_mode=testing_mode,
                                 timeout=dev_timeout)

        reg._dev_factory = _hire
        return reg
