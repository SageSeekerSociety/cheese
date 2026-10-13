# deploy/ — deployment tests

Read [`../AGENTS.md`](../AGENTS.md) first, especially the dev/production boundary.

After a merge makes a shell test fail, compare its expected log line with the
fake's current output before changing deployment behaviour. The app-tier tests
use `grep -Fqx` against `tests/fakes/app-tier/docker`; a new field in the fake's
line can leave a branch's exact-match assertion stale, but only update it after
checking that the intended deployment behaviour is unchanged.

Metering changes additionally follow
[`metering-proxy/AGENTS.md`](metering-proxy/AGENTS.md).
