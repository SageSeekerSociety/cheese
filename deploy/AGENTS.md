# deploy/ — deployment tests

Read [`../AGENTS.md`](../AGENTS.md) first, especially the dev/production boundary.

After a merge makes a shell test fail, compare its expected log line with the
fake's current output before changing deployment behaviour. The app-tier tests
use `grep -Fqx` against `tests/fakes/app-tier/docker`; a new field in the fake's
line can leave a branch's exact-match assertion stale, but only update it after
checking that the intended deployment behaviour is unchanged.

A compose service that reaches a process on the host must declare
`extra_hosts: ["host.docker.internal:host-gateway"]` (or the equivalent
`--add-host` for a container started directly). Without the mapping, the name
can resolve to the host's fake-IP range instead of the gateway and fail without
an application-level error. Keep the declaration with the service that needs it.

Metering changes additionally follow
[`metering-proxy/AGENTS.md`](metering-proxy/AGENTS.md).
