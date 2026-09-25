#!/usr/bin/env bash
# The tag an image built from <rev> is published under: the first seven
# characters of its full sha, which is what docker/metadata-action's
# `type=sha` writes. Never `git rev-parse --short=7`: that is a minimum, and it
# grows when another object in the clone shares the prefix, so a full clone and
# a shallow one name the same commit differently (2dab304e vs 2dab304).
set -euo pipefail
sha="$(git rev-parse --verify "${1:?usage: image-tag.sh <rev>}^{commit}")"
printf "%s\n" "${sha:0:7}"
