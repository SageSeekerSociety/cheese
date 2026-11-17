# The judge's Java 21 runtime (app/domain/judge/runtimes.py): a student's code
# runs here once per test case, in a container with no network, a read-only
# root and code, and uid 1000 (app/domain/judge/container.py). The base is
# pinned by digest so every judging of a 题目 runs on the same image. GNU time
# reports the CPU time and peak memory each case used.
FROM eclipse-temurin:21.0.5_11-jdk-noble@sha256:971bf64388e4fcf4a32cf87b5b19ed8e8a105e8f694b8718da9f3a128833dd7c
RUN apt-get update \
    && apt-get install -y --no-install-recommends time \
    && rm -rf /var/lib/apt/lists/* \
    && (getent passwd 1000 >/dev/null || useradd --uid 1000 --user-group --create-home --shell /bin/sh judge)
ENV LANG=C.UTF-8
USER 1000:1000
WORKDIR /code
