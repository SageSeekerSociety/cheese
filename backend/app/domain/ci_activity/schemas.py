"""Response payloads published by the CI activity read API."""

from typing import Literal

from pydantic import BaseModel


class CiSummary(BaseModel):
    n: int
    mean: float | None
    p50: float | None
    p90: float | None
    max: float | None


class CiSweepStatus(BaseModel):
    at: str
    error: str
    jobs_owed: int
    covered_from: str | None
    complete: bool


class CiRoundsBin(BaseModel):
    rounds: int
    prs: int


class CiRounds(BaseModel):
    prs: int
    pr_rounds: CiSummary
    queue_rounds: CiSummary
    more_than_one: float | None
    histogram: list[CiRoundsBin]
    covered_from: str | None
    incomplete_prs: int


class CiFailThenPass(BaseModel):
    runs: int
    of: int
    share: float | None


class CiBusyRunners(CiSummary):
    share: dict[Literal["pr", "queue", "main", "other"], float | None]


class CiHeadline(BaseModel):
    merge_queue_minutes: CiSummary
    runner_wait_seconds: dict[Literal["all", "hosted", "self_hosted"], CiSummary]
    ci_required_minutes: dict[Literal["pull_request", "merge_group"], CiSummary]
    rounds: CiRounds
    fail_then_pass: CiFailThenPass
    busy_runners: CiBusyRunners


class CiDaily(BaseModel):
    day: str
    merged: int
    merge_queue_p50: float | None
    merge_queue_p90: float | None
    runner_wait_hosted_p50: float | None
    runner_wait_self_hosted_p50: float | None
    ci_required_pr_p50: float | None
    ci_required_queue_p50: float | None


class CiHourly(BaseModel):
    hour: str
    busy_mean: float
    busy_peak: int
    pr_mean: float
    queue_mean: float
    main_mean: float
    other_mean: float
    hosted_mean: float
    self_hosted_mean: float
    queue_depth_peak: int


class CiHourOfDay(BaseModel):
    hour: int
    busy_mean: float | None
    busy_peak: int | None
    pr_mean: float | None
    queue_mean: float | None
    main_mean: float | None
    other_mean: float | None


class CiRunner(BaseModel):
    runner: str
    kind: Literal["hosted", "self_hosted"]
    jobs: int
    failed: int
    busy_hours: float
    utilisation: float | None
    wait_p50_s: float | None
    duration_p50_s: float | None


class CiEjection(BaseModel):
    reason: str
    actor: str
    count: int


class CiBoardOut(BaseModel):
    repo: str
    days: int
    since: str
    until: str
    generated_at: str
    sweep: CiSweepStatus | None
    headline: CiHeadline
    daily: list[CiDaily]
    hourly: list[CiHourly]
    hour_of_day: list[CiHourOfDay]
    runners: list[CiRunner]
    ejections: list[CiEjection]


class CiJobRow(BaseModel):
    name: str
    workflow_path: str
    workflow: str
    jobs: int
    failed: int
    duration_s: CiSummary
    wait_p50_s: float | None
    busy_hours: float


class CiJobsOut(BaseModel):
    jobs: list[CiJobRow]


class CiStepRow(BaseModel):
    name: str
    number: float | None
    duration_s: CiSummary


class CiStepsOut(BaseModel):
    job: str
    workflow: str
    steps: list[CiStepRow]
