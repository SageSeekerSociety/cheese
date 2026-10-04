# MicroCloud: where a cloud host comes from, what cheese asks of it, and how to ship a change there

Cheese's **Cloud** compute (see `where-a-turn-runs.md`) is a sandbox per agent session,
on a pool of hosts the platform opens on [MicroCloud](https://github.com/micro-teams/micro-cloud).
MicroCloud is Lg's team's project; cheese is one tenant of it. This file is the
cheese-side view: what we ask it for, what its answers mean, how the pool is kept, and the
procedure for getting a change into MicroCloud when cheese needs one. MicroCloud's own
architecture (Proxmox, LXC vs VM, its built-in AI channels) is in its README and is not
repeated here.

## The host pool

A host belongs to the platform, never to a project, room or session
(`backend/app/domain/machine/services.py`, `HostPool`). A session that needs hands on cloud
is given a home — its sandbox's directory — on any host with a free slot, whichever project
it is from: hosts that can run it now first, and among those the fullest, so the others can
empty and go. Only when no host has room does the pool take a ready warm machine, and only
when the warm pool is empty too does it create a host, and the session waits for it. Users
see their sandbox, never the host: the room hears 「正在准备沙箱」 and 「沙箱已就绪」, and no
user-facing route lists hosts. The admin dashboard counts them.

| Setting | Default | What it does |
|---|---|---|
| `CLOUD_HOST_SLOTS_PER_CORE` | 2 | Sandbox slots per host core. A slot is a running sandbox; one asleep holds none. |
| `CLOUD_SANDBOX_DISK_GB` | 5 | The disk budgeted to one home. A host keeps at most `disk_gb // this` homes, running or asleep, and never fewer than its slots. |
| `CLOUD_SANDBOX_IDLE_STOP_S` | 600 | A sandbox whose room runs no turn and whose session asked for no tool this long is put to sleep. |
| `CLOUD_SANDBOX_BACKGROUND_CAP_S` | 3600 | A command the executor still runs for the session (a Bash call sent to the background) keeps an idle sandbox up, until this long after the session's last activity. |
| `CLOUD_SANDBOX_ARCHIVE_AFTER_S` | 604800 | A home asleep this long is archived to the private bucket and deleted from its host. |
| `CLOUD_POOL_MIN_FREE_SLOTS` | 2 | When the free slots of the live hosts fall below this, the pool sweep adds a host ahead of demand. An idle host is not let go if that would take the free slots below it. |
| `CLOUD_HOST_IDLE_HOLD_S` | 1800 | How long a host that runs no sandbox is kept. Then its sleeping homes are archived and it is released. |
| `CLOUD_POOL_MAX_HOSTS` | 20 | The platform's cap on hosts, protecting the cluster. Draining hosts do not count. A session that finds the pool full and every host full is told capacity is tight and to try later. |
| `CLOUD_SANDBOX_CREDITS_PER_HOUR` | none | Credits a running sandbox costs per hour. Unset, no cloud sandbox starts. |
| `CLOUD_VM_CREDITS_PER_HOUR` | `{}` | Credits per hour of a whole cloud VM, by spec name. A spec not listed cannot start. |

Hosts are created at `MICROCLOUD_DEFAULT_CORES` / `_MEMORY_MB` / `_DISK_GB` (clamped into the
offering), so a host runs `cores × CLOUD_HOST_SLOTS_PER_CORE` sandboxes at once. A host has a
free slot for a new session when it has both a slot to run in and room on its disk for the
home. There is no team or project quota on cloud. A running sandbox is charged in credits by
the hour, from the same packs as model calls (`docs/manual/dev/billing.md`, 云端算力).

### Sleep, wake, archive

`backend/app/domain/machine/lifecycle.py`; the host side is `machine/sandbox_home.py`.

- **Idle** is no turn running in the session's room and no tool asked for in
  `CLOUD_SANDBOX_IDLE_STOP_S`, counted from the later of the session's last tool call and
  the end of the room's last turn. Turns are per room, so any turn keeps every sandbox of
  the room awake. A command the executor still runs for the session (a Bash call sent to
  the background; the executor reports `running_commands`) keeps it up, but only until
  `CLOUD_SANDBOX_BACKGROUND_CAP_S`. A process the agent detached itself, such as a dev
  server started with `nohup … &`, keeps nothing up.
- **Asleep**: the executor is stopped and whatever still holds the home (a dev server) is
  ended. The home stays on the host and holds no slot. The room hears
  「沙箱 N 分钟没有活动，已休眠……」. The next tool call starts the executor again on the same
  host through the ordinary install path, and the room hears 「正在唤醒沙箱」, then
  「沙箱已就绪」. When that host has no free slot, the home is archived from it and restored
  on a host that has one.
- **Archived**: the host writes the home to one `.tar.gz` and PUTs it to the private bucket
  (`TRANSCRIPT_S3_BUCKET`) through a URL signed for that one object; the backend compares the
  size and MD5 the host wrote with the bucket's size and ETag, and only then deletes the home
  from the host. An archive that does not verify is deleted and the home stays. The next tool
  call places the session on any host, which downloads and checks the archive and unpacks it
  before the executor starts (「正在从归档恢复沙箱」); the object is deleted once restored.
  Code is truth in git — every turn's Stop checkpoint has already run `cheese sync --all` —
  and the archive is a cache of the rest: uncommitted files, the environment, the build. With
  no private bucket configured nothing is archived and homes stay on their hosts.

A host is released once it has run no sandbox for `CLOUD_HOST_IDLE_HOLD_S` and holds no
home. One whose sleeping homes are still on its disk is set *draining* at that point: it takes
no new session, its homes are archived, and then it is released. A session that switches away
after pushing gives its home back; one that leaves without pushing (`abandon_unpushed`) keeps
its home, on its host or in its archive, until its room's cleanup removes it. A host the
provider fails before it is enrolled holds nothing of anyone's: the pool gives it up, deletes
it, and places its sessions again; after three such failures within an hour it stops creating
hosts for the rest of the hour. An enrolled host in `error` keeps its sessions.

Machines that a room or a session rented for itself before the pool were adopted as
*draining* hosts by the migration that introduced it: they keep the sessions on them, take
no new one, and are released like any other host once no home is left on them.

## What cheese asks for

One create call (`MicroCloudClient.create_machine`, built in `HostPool._create`) carries:

- the platform's own MicroCloud customer (`cheese-platform-host-pool`) and its fund account
  (`MICROCLOUD_ACCOUNT_NAME`, topped up to `MICROCLOUD_INITIAL_FUNDS`);
- the offering (machine type + zone + template; `MICROCLOUD_OFFERING_ID`, or the first
  active one), and the host size above clamped into that offering's range;
- `aiMode: none`. The host only executes tools; the models its sessions use come from
  the session host, so it needs no AI channel of MicroCloud's. Without the field MicroCloud
  would wire its default channel onto the machine;
- `sshPubkey`: two keys on separate lines. A one-shot bootstrap key the platform uses
  once to enrol the machine (erased at enrolment), and
  `MICROCLOUD_OPERATOR_SSH_PUBKEY` so an operator can still log in afterwards. On dev the
  operator key is the dev box's own, so `ssh cheese@<machine ip>` from the dev box works.

After create, the pool sweep (`CloudPoolSweeper`, every
`MACHINE_ENROLL_INTERVAL_SECONDS` = 10 s) does the rest with no human: refresh hosts still
changing, re-check settled ones every `MICROCLOUD_RECONCILE_INTERVAL_S` (120 s), enrol every
host that is `running` (mint a device credential owned by the platform's pool identity, ssh
in with the bootstrap key, install the connector as a service), then size the pool: release
idle hosts, give up on failed ones, add one ahead of demand, and tell every room still
waiting on a sandbox whose host is up. Putting sandboxes to sleep and archiving homes is a
second loop on the same interval (`SandboxSweeper`), so an archive that takes minutes does
not hold up enrolment.

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
Enrolment waits only for `status: running`. A host that reports `status: error` before
it was enrolled is given up as described above. Quota, offering and spec problems are
refused at create time, so this `error` means building the machine failed.

Since micro-cloud#84 every machine has an event log at `GET /machine/{id}/events` (tenant
secret, page parameters, optional `since`): every Proxmox task with its UPID and duration,
ssh reachable, init done with the script's output tail, RUNNING, and every failure with
its exception. It outlives the machine (machines are soft-deleted), so a stuck or failed
lease is read there first, before anyone asks for a log.
`tmp/cloud-diag/dev_mc_lxc_probe.py` prints it after a probe.

## Warm capacity

`MICROCLOUD_WARM_POOL_SIZE` sets the number of unused default CPU machines prepared by
this deployment (0 disables replenishment; maximum 5). These machines use a platform
account and have no team binding. When the host pool needs another host it takes one: a
durable reservation of the host is written first, then MicroCloud is asked to claim the
machine into the platform's host account under the host's id as `claimKey`, and only
then is the connected device handed to the pool's identity. A claim whose answer was lost
is retried as the same claim; one refused five times leaves the host failed, which the
pool gives up.

The pool worker runs separately from ordinary machine enrollment. It resumes
interrupted creation and claims, retires unused machines after
`MICROCLOUD_WARM_MAX_AGE_SECONDS`, and waits for provider deletion before replacing
them. Claimed machines never return to the pool. After five failed cleanup attempts,
the record is kept and never retried, since its machine may still be billed. It does
not take a place in the pool, but once three such records exist the pool stops
replacing machines and logs an error until they are resolved at the provider. The
worker reads each such record back from the provider and closes it once the provider
no longer has the machine, so removing it at the provider is all a person has to do.
A record whose error starts with `Quarantined` was set aside by a person and is never
closed on its own.

It prepares the deployment's default offering at the host size, with `aiMode: none`.
Measure command readiness separately from project
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
