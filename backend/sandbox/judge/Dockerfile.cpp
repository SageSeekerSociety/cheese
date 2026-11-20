# The judge's C++17 (g++ 13) runtime (app/domain/judge/runtimes.py): a student's code
# runs here once per test case, in a container with no network, a read-only
# root and code, and uid 1000 (app/domain/judge/container.py). The base is
# pinned by digest so every judging of a 题目 runs on the same image. GNU time
# reports the CPU time and peak memory each case used.
FROM gcc:13.3.0-bookworm@sha256:1d71f0f3450214bef38fe09e6f610fb6cca90cf97b43f4ce845bfc32a4168818
RUN apt-get update \
    && apt-get install -y --no-install-recommends time \
    && rm -rf /var/lib/apt/lists/* \
    && (getent passwd 1000 >/dev/null || useradd --uid 1000 --user-group --create-home --shell /bin/sh judge)
# Test scripts (kind SCRIPT) in C++ bring their own harness: the setter's test
# program writes the JUnit XML report to "$JUDGE_REPORT" itself.
ENV LANG=C.UTF-8
USER 1000:1000
WORKDIR /code
