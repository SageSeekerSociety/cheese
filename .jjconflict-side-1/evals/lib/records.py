"""Core datatypes for the agent eval framework.

A Scenario is declarative: an id, a rubric (for the judge), and an async `run`
that drives the isolated backend through its real API and returns evidence +
deterministic platform checks. The runner owns lifecycle, judging, reporting.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from evals.lib.context import EvalContext


@dataclass
class Check:
    """One deterministic platform-side assertion (🔧 half of a scenario)."""

    name: str
    passed: bool
    detail: str = ""


@dataclass
class ScenarioOutcome:
    """What a scenario's `run` hands back to the runner.

    - inputs: EVERYTHING that was fed in (seeded memory, messages, API calls) —
      full inputs ride the results.jsonl so any run is re-inspectable/re-runnable.
    - evidence: structured outputs collected from the system (blocks, docs, WS
      frames, turn records).
    - checks: deterministic assertions already evaluated by the scenario.
    - judge_evidence: the markdown handed to the judge (None → no judge call).
    """

    inputs: dict[str, Any]
    evidence: dict[str, Any]
    checks: list[Check]
    judge_evidence: str | None = None


@dataclass(frozen=True)
class Scenario:
    """A declarative eval scenario (one module in evals/scenarios/)."""

    id: str
    name: str
    description: str
    run: Callable[["EvalContext"], Awaitable[ScenarioOutcome]]
    # Judge rubric (0-2 scale). None → purely deterministic scenario (no judge).
    rubric: str | None = None
    timeout_s: float = 600.0


@dataclass
class JudgeVerdict:
    """Structured judge output (requested as strict JSON from the model)."""

    score: int  # 0 | 1 | 2
    reason: str
    model: str
    raw_response: str
    prompt: str


@dataclass
class ScenarioResult:
    """Final per-scenario record: everything needed for the report + jsonl."""

    scenario_id: str
    name: str
    verdict: str  # PASS | PARTIAL | FAIL
    started_at: str
    duration_s: float
    checks: list[Check] = field(default_factory=list)
    judge: JudgeVerdict | None = None
    inputs: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario_id,
            "name": self.name,
            "verdict": self.verdict,
            "started_at": self.started_at,
            "duration_s": self.duration_s,
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail}
                for c in self.checks
            ],
            "judge": None
            if self.judge is None
            else {
                "model": self.judge.model,
                "score": self.judge.score,
                "reason": self.judge.reason,
                "prompt": self.judge.prompt,
                "raw_response": self.judge.raw_response,
            },
            "inputs": self.inputs,
            "evidence": self.evidence,
            "error": self.error,
        }
