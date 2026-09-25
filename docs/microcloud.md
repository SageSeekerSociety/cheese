# MicroCloud: where a Cloud machine comes from, what cheese asks of it, and how to ship a change there

Cheese's **Cloud** compute (see `where-a-turn-runs.md`) is one machine per topic, opened
on [MicroCloud](https://github.com/micro-teams/micro-cloud) and released when the topic is
archived. MicroCloud is Lg's team's project; cheese is one tenant of it. This file is the
cheese-side view: what we ask it for, what its answers mean, and the procedure for getting a
change into it when cheese needs one. MicroCloud's own architecture (Proxmox, LXC vs VM,
its built-in AI channels) is in its README and is not repeated here.

## What cheese asks for

`backend/app/domain/machine/` holds the whole exchange. One create call
(`MicroCloudClient.create_machine`, built in `MachineService.provision`) carries:

- the project's MicroCloud customer and fund account (`customerId`, `accountId`);
- the offering (machine type + zone + template; `MICROCLOUD_OFFERING_ID`, or the first
  active one), and cores / memory / disk clamped into that offering's range;
- `aiMode: none`. The machine only executes tools; the models its sessions use come from
  the session host, so it needs no AI channel of MicroCloud's. Without the field MicroCloud
  would wire its default channel onto the machine;
- `sshPubkey`: three keys on separate lines. A one-shot bootstrap key the platform uses
  once to enrol the machine (erased at enrolment), the requesting human's key if any, and
  `MICROCLOUD_OPERATOR_SSH_PUBKEY` so an operator can still log in afterwards. On dev the
  operator key is the dev box's own, so `ssh cheese@<machine ip>` from the dev box works.

After create, the enrolment sweep (`MachineEnrollmentSweeper`, every
`MACHINE_ENROLL_INTERVAL_SECONDS` = 10 s) does the rest with no human: refresh unsettled
machines and enrol every machine that is `running`: mint a device credential, ssh in with
the bootstrap key, install the connector as a service. Ready leases
then go to `CloudWakeup`, which delivers the message the room has been holding. The
connector route also wakes the topic the moment the device attaches, so the room does not
wait for the next tick.

Timings on dev, MicroCloud main after micro-cloud#82, #83 and #84 (2026-09-03): an LXC
machine is `running` 25 s after the create call (event log of machine 752: `pct create`
14 s, ssh reachable 6 s later, init 2 s); a VM was `running` at 56 s (703, before the
node's disk recovered, see `infrastructure.md` on pve119). The `pct create` time is
Proxmox extracting the 405 MB template onto its thin pool and moves with that disk's
load: 37 s on 702 an hour earlier. Before #83 every machine also downloaded Claude Code
from claude.ai during init (49 s); the template carries the binary now and init copies
it. On top of that, cheese's sweep notices the running machine within 10 s and enrolment
takes a few seconds. The next lever on MicroCloud's side is cloning a base container
instead of extracting the template per machine.

## Two machine states, not one

MicroCloud reports `status` (provisioning / starting / running / stopping / stopped /
error) and, separately, `aiStatus` (disabled / provisioning / ready / error). A machine
created with `aiMode: none` reports `aiStatus: disabled` from the moment it exists.
Enrolment waits only for `status: running`. A room's lease counts a machine as ready
when it is `running`, enrolled, and its `aiStatus` is `ready` or `disabled`; an `error`
in either field is handed to the room as a failed lease.

Since micro-cloud#84 every machine has an event log at `GET /machine/{id}/events` (tenant
secret, page parameters, optional `since`): every Proxmox task with its UPID and duration,
ssh reachable, init done with the script's output tail, RUNNING, and every failure with
its exception. It outlives the machine (machines are soft-deleted), so a stuck or failed
lease is read there first, before anyone asks for a log.
`tmp/cloud-diag/dev_mc_lxc_probe.py` prints it after a probe.

## Warm capacity

`MICROCLOUD_WARM_POOL_SIZE` sets the number of unused default CPU machines prepared by
this deployment (0 disables replenishment; maximum 5). These machines use a platform
account and have no team binding. The room's existing permission and quota checks run
before a durable reservation is written. The reservation counts against team quota
while MicroCloud confirms the claim. Only then does Cheese bind the connected device
to the room's team.

The pool worker runs separately from ordinary machine enrollment. It resumes
interrupted creation and claims, retires unused machines after
`MICROCLOUD_WARM_MAX_AGE_SECONDS`, and waits for provider deletion before replacing
them. Claimed machines never return to the pool. After five failed cleanup attempts,
the retained record prevents replacement from hiding an unresolved billed machine.

This first version prepares the deployment's default offering with `aiMode: none`.
It does not prepare every cloud specification. Configure a small pool only after the
provider claim endpoint is deployed. Measure command readiness separately from project
setup and model response; no startup latency has been established by the functional tests.

## Shipping a change to MicroCloud

Follow the upstream [release procedure](https://github.com/micro-teams/micro-cloud/blob/main/RELEASING.md)
and [deployment instructions](https://github.com/micro-teams/micro-cloud/blob/main/deploy/README.md).
The normal merge queue requires a code-reviewers approval. The upstream release
procedure also documents a direct REST merge path for an authorized maintainer.
Use the permitted release path after CI passes; push access alone is not evidence
that every merge method is available.

Generate Kotlin API models from `MicroCloud-API.yml`, run the Maven build with
PostgreSQL, and retain the generated schema changes. Deploy the approved main-branch
bundle before enabling Cheese's warm pool. The documented deployment route hands the
build artifact to the MicroCloud operations agent; follow the session's authorization
rules before sending that message. Template changes require a separate upload, as
described upstream.
