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

Timings on dev, MicroCloud main after micro-cloud#82 and #83 (2026-09-03): an LXC machine is
`running` 44 s after the create call and its AI channel `ready` at 52 s; a VM is `running` at
56 s and `ready` at 65 s. Of the LXC's 44 s about 37 are Proxmox extracting the 405 MB template
onto its thin pool, 4 boot, 3 the init script. Before #83 every machine also downloaded Claude
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
(machine 553 on dev, 8½ minutes, then destroyed with the topic); its ccproxy record went
with it, so keep such a machine and ask the ops agent for that record before archiving.

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

## Shipping a change to MicroCloud

MicroCloud has no written release procedure of its own; this is the one Lg described and
that was run end to end for 0.4.1 (2026-09-02).

1. **Branch on the fork, open the PR upstream.** Fork `andylizf/micro-cloud`, PR against
   `micro-teams/micro-cloud`. Kotlin/Spring; interfaces are generated from
   `MicroCloud-API.yml`, so an API change starts there. Tests need a Postgres on :5432 with
   schema `microcloud` (`ddl-auto=update`), e.g. a throwaway pgserver.
2. **Merge it yourself.** Lg's rule: merge when you judge it ready; there is no review
   wait. Branch protection refuses `gh pr merge`, so use the REST endpoint:
   `gh api -X PUT repos/micro-teams/micro-cloud/pulls/<n>/merge -f merge_method=merge`.
3. **Take the bundle from main's CI.** The main build uploads one artifact (retention 1 day).
   The ops agent cannot use the artifact page (it needs a GitHub login), so hand it the
   direct download URL: the `Location` of
   `curl -sI -H "Authorization: Bearer $(gh auth token)" https://api.github.com/repos/micro-teams/micro-cloud/actions/artifacts/<id>/zip`
   That is an Azure SAS URL that expires within minutes, so fetch it right before sending.
4. **Hand it to the ops agent.** The only MicroCloud operator is the agent `MicroCloud运维`
   on [microteams.app](https://microteams.app), group chat `/chats/252`; the message is one line:
   「更新 <url>」. It downloads, deploys to `microcloud-prod`, checks the
   service, and reports in the chat. Its download truncated once at 41 MB with HTTP 200;
   it resumes with `curl -C -` and checks `unzip -t`, and it helps to say so. The site is
   Flutter web: through web-plane (profile `main`, lane `microteams`), enable semantics
   with `eval "document.querySelector('flt-semantics-placeholder').click()"` before the
   first `snapshot`.
5. **Templates do not follow the bundle by themselves.** An LXC machine runs the
   `init-machine.py` **baked into the rootfs template on Proxmox** (pve119,
   `local:vztmpl/debian13.tar.zst`), and since micro-cloud#83 both templates also carry the
   Claude Code binary, so a change under `templates/` needs the LXC template re-uploaded
   (CI builds it on every push to `main` and nightly; the ops agent uploads with
   `POST /machine/template/<id>/upload {placementId}`) and the VM template re-baked (the
   agent does that from the console, about four minutes). Proxmox refuses to overwrite the
   LXC file, so the old one has to be moved away first by root on pve119 (into a dated
   `cache.bak-*` directory next to it); the ops agent's token is scoped to the cheese-dev
   pool and cannot do that.
6. **Verify on dev**, then **bump and release.** Once the change has run stably: bump the
   patch version with `scripts/version.sh <x.y.z>`, PR, merge, hand that bundle to the ops
   agent the same way, then
   `gh release create v<x.y.z> --repo micro-teams/micro-cloud --target <merge sha> --generate-notes`.

Cheese follows the moment MicroCloud is deployed: nothing on our side pins a MicroCloud
version, so a MicroCloud regression shows up in the next Cloud topic on dev.
