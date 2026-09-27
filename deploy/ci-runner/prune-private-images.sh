#!/usr/bin/env bash
# Remove the private executor images a CI run has superseded.
#
# Every `remote / private-chat` run loads a fresh build under the same tag,
# which leaves the previous image untagged. Only untagged images carrying the
# build's label go: a job reaches its image by tag, so an untagged one is out
# of every job's reach, and prune skips any a container still holds.
#
# The Docker daemon runs one image prune at a time and refuses a second with
# "a prune operation is already running". Two slots on one box finish this job
# within the same second often enough to hit that, so the prune waits on a
# host-wide lock instead. Treating the refusal as success would be wrong: the
# prune already running chose its images before this job released its own
# container, so it can skip the image this job just let go of.
set -euo pipefail

exec 9>"${CHEESE_DOCKER_PRUNE_LOCK:-/tmp/cheese-ci-docker-prune.lock}"
flock 9
docker image prune --force --filter label=cheese.ci.private-executor=true
