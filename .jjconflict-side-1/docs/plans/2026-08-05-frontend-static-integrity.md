# Frontend Static Integrity Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prevent a frontend image or running container from reporting healthy when `index.html` references missing Vite assets.

**Architecture:** A single POSIX-shell checker validates `index.html` against the local asset tree. Docker build, image health, and Compose health all invoke that checker so the deployer's existing healthy/rollback gate needs no duplicate parser.

**Tech Stack:** POSIX shell, Docker, Docker Compose, nginx, Vite.

---

### Task 1: Specify the integrity contract with executable tests

**Files:**
- Create: `frontend/scripts/test-check-static-assets.sh`

**Step 1: Write the failing test**

Create fixtures for a complete `index.html`, one missing CSS asset, an HTML file with no JS/CSS entrypoints, and an absent `index.html`. Assert the checker accepts only the complete fixture and preserves query-string handling.

**Step 2: Run test to verify it fails**

Run: `sh frontend/scripts/test-check-static-assets.sh`

Expected: FAIL because `frontend/scripts/check-static-assets.sh` does not exist.

### Task 2: Implement the reusable checker

**Files:**
- Create: `frontend/scripts/check-static-assets.sh`

**Step 1: Implement minimal validation**

Extract quoted `src` and `href` values, normalize `/assets/`, `assets/`, and `./assets/` references, remove query and fragment suffixes, and fail for missing files or absent JavaScript/CSS entrypoints.

**Step 2: Run tests**

Run: `sh frontend/scripts/test-check-static-assets.sh`

Expected: all fixture cases print PASS.

### Task 3: Make bad images unbuildable and unhealthy

**Files:**
- Modify: `frontend/Dockerfile`
- Modify: `.github/workflows/build.yml`
- Modify: `deploy/compose/docker-compose.base.yml`
- Modify: `deploy/compose/docker-compose.etrip.yml`
- Modify: `deploy/docker-compose.prod.yml`

**Step 1: Add the build gate and image healthcheck**

Run the fixture tests before the frontend image build, copy the checker into the nginx stage, run it against the copied `dist`, and define an image healthcheck that combines the checker with an HTTP probe.

**Step 2: Replace Compose HTTP-only probes**

Make each frontend healthcheck execute the same file validation before `curl` so Compose does not override the stronger image healthcheck with the old root-only probe.

**Step 3: Verify Compose rendering**

Run `docker compose config --quiet` for each changed Compose file with the minimal required environment files.

Expected: all configurations parse successfully.

### Task 4: Regression verification and commit

**Files:**
- Test: `frontend/scripts/test-check-static-assets.sh`
- Test: `deploy/tests/test-app-tier-health.sh`

**Step 1: Run shell regressions**

Run the checker tests and `bash deploy/tests/test-app-tier-health.sh all`.

Expected: all cases pass, including deploy rejection of an unhealthy frontend status.

**Step 2: Build and inspect the image**

Run `docker build --target prod -t cheese-frontend:static-integrity-test frontend` and inspect the configured healthcheck.

Expected: image build succeeds, the checker reports all emitted references present, and Docker records the combined health command.

**Step 3: Commit**

Commit the design, test, implementation, Docker, and Compose changes together with a focused fix message.
