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
ENV LANG=C.UTF-8 GOTOOLCHAIN=local GOFLAGS=-buildvcs=false
USER 1000:1000
WORKDIR /code
