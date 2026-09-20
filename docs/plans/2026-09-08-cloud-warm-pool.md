# Cloud warm pool implementation plan

**Goal:** Move standard CPU machine preparation ahead of room execution while preserving team quotas and one machine per room.

**Architecture:** Cheese maintains a bounded deployment pool of unused machines and enrolls them before assignment. MicroCloud records a one-time claim that changes customer and billing ownership within the same tenant. A claimed machine never returns to the pool. Empty pools use on-demand provisioning.

**Tech stack:** Kotlin/Spring and PostgreSQL in MicroCloud; Python/SQLAlchemy and PostgreSQL in Cheese.

1. Extend `MicroCloud-API.yml`, `MachineEntity.kt`, `MachineService.kt` and `MachineController.kt` with an idempotency key for creating warm machines and a one-time claim endpoint. Validate tenant ownership, target accounts, offering permission and readiness. Retried claims must match the original recipient. Generate API models from the specification. Test cross-tenant access, concurrent claims, retries and rejection of claims for ordinary machines.
2. Add a separate warm-machine record in `backend/app/domain/machine/models.py` with an Alembic migration. Persist creation identity and claim intent before provider calls so interrupted operations can resume. Unassigned capacity belongs to the platform and is excluded from team quotas; a reserved room lease counts immediately.
3. Add `warm.py` and integrate `services.py` and `runner.py`. Prepare the default CPU specification, enroll without team access, require an online connector before selection, reserve under the existing team quota and topic locks, then complete the provider claim and bind the device. A failed or interrupted claim stays reserved for its original room. Delete failed or expired unused machines with bounded retries. Never recycle assigned machines.
4. Add bounded pool settings in `app/core/config.py`. Start with a small explicit deployment target; do not prewarm every offering or GPU specification. Record pool availability, claim duration and cold starts without credentials.
5. Run functional tests on mac mini in isolated checkouts and databases with limited concurrency. Deploy MicroCloud before Cheese. Measure command readiness separately from project setup and model response. Report observed latency; three seconds is a target until measured.
