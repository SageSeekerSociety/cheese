# CheeseX Disk Pressure Guard Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prevent the CheeseX dev root filesystem from reaching 100% by safely reclaiming only reconstructible Agent runtime state.

**Architecture:** A root-owned systemd timer runs a small fail-closed shell guard every five minutes. The guard acts only above a configurable utilization threshold and after two zero-active-turn health checks; it removes explicitly labelled tmux sandboxes and old, narrowly matched backend temp directories.

**Tech Stack:** POSIX shell, Docker CLI, curl, Python 3 JSON parsing, systemd, shell regression tests.

---

### Task 1: Specify guard behavior with fakes

**Files:**
- Create: `deploy/tests/test-disk-pressure-guard.sh`

**Step 1:** Add fake `df`, `curl`, `docker`, and `logger` commands in a temporary directory.

**Step 2:** Add a below-threshold case and assert Docker is never called.

**Step 3:** Add an active-turn case and assert cleanup is deferred.

**Step 4:** Add a pressure/idle case and assert labelled container removal and old-temp cleanup are requested.

**Step 5:** Run `bash deploy/tests/test-disk-pressure-guard.sh`; expect failure because the guard does not exist.

### Task 2: Implement the fail-closed guard

**Files:**
- Create: `deploy/cheesex-disk-pressure-guard.sh`

**Step 1:** Parse root utilization and return below 85%.

**Step 2:** Parse the backend health response and require zero active turns twice.

**Step 3:** Remove only containers selected by `label=cheesex-tmux=1`.

**Step 4:** Remove only six-hour-old `/tmp/tmp.*` and `/tmp/pytest-of-*` directories inside `cheese-backend-1`.

**Step 5:** Log the before/after utilization and run the shell test; expect all cases to pass.

### Task 3: Schedule and verify

**Files:**
- Create: `deploy/systemd/cheesex-disk-pressure-guard.service`
- Create: `deploy/systemd/cheesex-disk-pressure-guard.timer`

**Step 1:** Add a root oneshot service with filesystem hardening and Docker dependencies.

**Step 2:** Add a persistent five-minute timer with jitter.

**Step 3:** Run `bash -n` on the scripts and `systemd-analyze verify` on the units.

**Step 4:** Install the files on the dev box, enable the timer, and run the service once.

**Step 5:** Verify the timer is active, the service exits cleanly below threshold, backend health is green, and skill-copy regression still passes.
