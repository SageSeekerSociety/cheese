# The judge's Go 1.23 runtime (app/domain/judge/runtimes.py): a student's code
# runs here once per test case, in a container with no network, a read-only
# root and code, and uid 1000 (app/domain/judge/container.py). The base is
# pinned by digest so every judging of a 题目 runs on the same image. GNU time
# reports the CPU time and peak memory each case used.
FROM golang:1.23.4-bookworm@sha256:95db116434e3f21a2a15600ffc7169bf380c6bfd021b154d106fcb346721c277
RUN apt-get update \
    && apt-get install -y --no-install-recommends time \
    && rm -rf /var/lib/apt/lists/* \
    && (getent passwd 1000 >/dev/null || useradd --uid 1000 --user-group --create-home --shell /bin/sh judge)
# Test scripts (kind SCRIPT) run offline, so gotestsum is built in at a pinned
# version (the Go checksum database vouches for it) and writes JUnit XML:
#   gotestsum --junitfile "$JUDGE_REPORT" -- ./...
RUN GOBIN=/usr/local/bin GOFLAGS= go install gotest.tools/gotestsum@v1.12.0 \
    && rm -rf /root/go /root/.cache
ENV LANG=C.UTF-8 GOTOOLCHAIN=local GOFLAGS=-buildvcs=false
USER 1000:1000
WORKDIR /code
