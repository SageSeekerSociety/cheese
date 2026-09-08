# MicroCloud: where a Cloud machine comes from, how its Claude gets wired, and how to ship a change there

Cheese's **Cloud** compute (see `where-a-turn-runs.md`) is one machine per topic, opened
on [MicroCloud](https://github.com/micro-teams/micro-cloud) and released when the topic is
archived. MicroCloud is Lg's team's project; cheese is one tenant of it. This file is the
cheese-side view: what we ask it for, what its answers mean, and the procedure for getting a
change into it when cheese needs one. MicroCloud's own architecture (Proxmox, LXC vs VM,
newapi, ccproxy, the switch) is in its README and is not repeated here.

## What cheese asks for

`backend/app/domain/machine/` holds the whole exchange. One create call
(`MicroCloudClient.create_machine`, built in `MachineService.provision`) carries:

- the project's MicroCloud customer and fund account (`customerId`, `accountId`, and the
  same account for `newapiAccountId` / `ccproxyAccountId`);
- the offering (machine type + zone + template; `MICROCLOUD_OFFERING_ID`, or the first
  active one), and cores / memory / disk clamped into that offering's range;
- `aiMode` (`MICROCLOUD_AI_MODE`, default `ccproxy`), so the machine is **born on the
  channel we want** (micro-cloud#78) instead of on newapi and switched later;
- `sshPubkey`: three keys on separate lines. A one-shot bootstrap key the platform uses
  once to enrol the machine (erased at enrolment), the requesting human's key if any, and
  `MICROCLOUD_OPERATOR_SSH_PUBKEY` so an operator can still log in afterwards. On dev the
  operator key is the dev box's own, so `ssh cheese@<machine ip>` from the dev box works.

After create, the enrolment sweep (`MachineEnrollmentSweeper`, every
`MACHINE_ENROLL_INTERVAL_SECONDS` = 10 s) does the rest with no human: refresh unsettled
machines, switch any machine that came up on the wrong channel (`reconcile_ai_mode`),
and enrol every machine that is `running` with its AI channel `ready`: mint a device
credential, ssh in with the bootstrap key, install the connector as a service. Ready leases
then go to `CloudWakeup`, which delivers the message the room has been holding. The
connector route also wakes the topic the moment the device attaches, so the room does not
wait for the next tick.

Timings on dev, MicroCloud main after micro-cloud#82, #83 and #84 (2026-09-03): an LXC machine
is `running` 25 s after the create call and its AI channel `ready` at 31 s (event log of
machine 752: `pct create` 14 s, ssh reachable 6 s later, init 2 s, ccproxy login 5 s); a VM
was `running` at 56 s and `ready` at 65 s (703, before the node's disk recovered, see
`infrastructure.md` on pve119). The `pct create` time is Proxmox extracting the 405 MB
template onto its thin pool and moves with that disk's load: 37 s on 702 an hour earlier. Before #83 every machine also downloaded Claude
Code from claude.ai during init (49 s); the template carries the binary now and init copies it.
On top of that, cheese's sweep notices the ready lease within 10 s, enrolment takes a few
seconds, and the first turn's own model latency was about 22 s to the first tool call, so a
Cloud topic's first reply lands roughly 1½ minutes after the message (2 m 31 s to 2 m 43 s on
2026-09-02, before #83). The next lever on MicroCloud's side is cloning a base container
instead of extracting the template per machine.

## Two machine states, not one

MicroCloud reports `status` (provisioning / starting / running / stopping / stopped /
error) and, separately, `aiStatus` (disabled / provisioning / ready / error). A machine is
`running` well before its Claude can be used, so cheese waits for **both** before enrolling
(`AiStatus` in `models.py` explains why the two are kept apart).

What each `aiStatus` means for a machine born on `ccproxy`:

| `aiStatus` | Meaning | What cheese does |
|---|---|---|
| `provisioning` | MicroCloud has registered the machine with ccproxy and started its login; ccproxy's login-operator is completing the subscription OAuth | waits; the room keeps showing 「机器正在创建」 |
| `ready` | Claude Code on the machine reaches a real Anthropic subscription | enrols, then delivers the held message |
| `error` | ccproxy refused the machine (its connector's preflight failed) or the login failed | the sweep posts one `cloud_provisioning` event with `state: failed` into the room (#668); the lease is not swapped for another machine |
| `disabled` | machine created with `aiMode: none` | never enrolled |

`error` three seconds after `running` was micro-cloud#78's bug: a ccproxy-born machine had
no `claude` binary, fixed in #79. `provisioning` that never ends has been seen once
(machine 553 on dev, 8½ minutes, then destroyed with the topic) and could not be explained
afterwards, because MicroCloud kept no record beyond the two status fields. Since
micro-cloud#84 every machine has an event log at `GET /machine/{id}/events` (tenant secret,
page parameters, optional `since`): every Proxmox task with its UPID and duration, ssh
reachable, init done with the script's output tail, the ccproxy registration, RUNNING, the
login request id and every change of ccproxy's reported login status, and every failure
with its exception. It outlives the machine (machines are soft-deleted now), so a stuck or
failed lease is read there first, before anyone asks for a log. `tmp/cloud-diag/dev_mc_lxc_probe.py`
prints it after a probe.

## What ccproxy is, from where cheese stands

[ccproxy](https://github.com/micro-teams/ccproxy) is the team's subscription relay. On a
`ccproxy` machine Claude Code talks to `api.anthropic.com` through a local proxy engine
that MicroCloud configured. It sets `HTTPS_PROXY` in the machine's `~/.claude/settings.json`
to a per-machine `user:password` that is the machine's identity at ccproxy. The engine
swaps that identity for the account's real subscription token, which never touches the
machine. The one-time OAuth for the subscription is done by a human login-operator on
ccproxy's side; MicroCloud only triggers it and polls.

Cheese reads that per-machine identity once, at enrolment, while it is on the machine over
ssh (`CHEESE_CCPROXY_UPSTREAM` in `enrollment.py`), and stores it on the machine row as
`ccproxy_upstream`: the metering proxy has to present this exact identity to relay this
machine's requests. A machine whose channel settled after enrolment is backfilled by the
sweep. Nothing in cheese holds a subscription credential.

The alternative mode, `newapi`, is MicroCloud's default when the create call carries no
`aiMode`: an LLM relay whose default routes to a cheap non-Claude model. That is why
`MICROCLOUD_AI_MODE` is `ccproxy` and why the sweep still switches a machine found on
`newapi`.

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

This first version prepares the deployment's default offering with ccproxy AI mode.
It does not prepare every cloud specification. Configure a small pool only after the
provider claim endpoint is deployed. Measure command readiness separately from project
setup and model response; no startup latency has been established by the functional tests.

## Shipping a change to MicroCloud

Follow the upstream [release procedure](https://github.com/micro-teams/micro-cloud/blob/main/RELEASING.md)
and [deployment instructions](https://github.com/micro-teams/micro-cloud/blob/main/deploy/README.md).
The current repository rules require an approval from its code-reviewers team and a
passing `test-compose` check. Push access alone does not satisfy that review requirement.

Generate Kotlin API models from `MicroCloud-API.yml`, run the Maven build with
PostgreSQL, and retain the generated schema changes. Deploy the approved main-branch
bundle before enabling Cheese's warm pool. The documented deployment route hands the
build artifact to the MicroCloud operations agent; follow the session's authorization
rules before sending that message. Template changes require a separate upload, as
described upstream.
