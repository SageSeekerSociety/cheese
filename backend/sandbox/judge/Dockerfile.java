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
# Test scripts (kind SCRIPT) run offline, so JUnit 5's console launcher is baked
# in at $JUNIT_JAR, pinned by version and by the hash Maven Central publishes:
#   java -jar $JUNIT_JAR execute -cp out --scan-classpath --reports-dir /tmp/r;
#   cp /tmp/r/TEST-junit-jupiter.xml "$JUDGE_REPORT"
# (";" not "&&": the launcher exits 1 when a test fails, and the report is
# still wanted then.)
ARG JUNIT_VERSION=1.11.4
ARG JUNIT_SHA256=b016ef6b1c3454d6d7c2c88ce081dabf289699686af6622d6e4e2e1b54b4a2fc
ADD https://repo1.maven.org/maven2/org/junit/platform/junit-platform-console-standalone/${JUNIT_VERSION}/junit-platform-console-standalone-${JUNIT_VERSION}.jar \
    /opt/junit/junit-platform-console-standalone.jar
RUN echo "${JUNIT_SHA256}  /opt/junit/junit-platform-console-standalone.jar" | sha256sum -c - \
    && chmod 0644 /opt/junit/junit-platform-console-standalone.jar
ENV LANG=C.UTF-8 JUNIT_JAR=/opt/junit/junit-platform-console-standalone.jar
USER 1000:1000
WORKDIR /code
