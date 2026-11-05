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
empty and go. Users see their sandbox, never the host: the room hears 「正在准备沙箱」 and
「沙箱已就绪」, and no user-facing route lists hosts. The admin dashboard counts them.

The pool has two ways to be sized. **Fixed** (`CLOUD_POOL_FIXED_HOSTS`, the default): the
sweep keeps that many ordinary hosts up whatever the demand — an idle one is not released
when its hold runs out, and hosts above the size are released by the usual rules until the
pool is back at it. A session that finds every host full waits for the next sweep, which adds
one. **Demand** (`CLOUD_POOL_FIXED_HOSTS=0`): as before, the pool adds a host when free
slots run low, takes a ready warm machine if there is one, and lets idle hosts go after the
hold. Either way the size and the cap are bounds, not promises: the provider failing, the
cap, or a host with no data disk can keep the pool short.

| Setting | Default | What it does |
|---|---|---|
| `CLOUD_HOST_SLOTS_PER_CORE` | 2 | Sandbox slots per host core. A slot is a sandbox, for as long as it exists. |
| `CLOUD_SANDBOX_IDLE_STOP_S` | 600 | A sandbox whose conversation runs no turn and whose session asked for no tool this long is destroyed. |
| `CLOUD_SANDBOX_BACKGROUND_CAP_S` | 900 | A command the executor still runs for the session (a Bash call sent to the background) keeps an idle sandbox up, until this long after the session's last activity. |
| `CLOUD_VM_BACKGROUND_CAP_S` | 3600 | The same keep-alive for a whole cloud VM's sandbox. Longer than a sandbox's, because a VM's idle release (1800 s) is longer than a sandbox's. |
| `CLOUD_POOL_FIXED_HOSTS` | 10 | How many ordinary hosts the pool keeps up whatever the demand (0 = off, the pool is sized by demand alone). The default leaves two of the cap's twelve places for whole cloud VMs. The sweep fills up to it, keeps an idle host past `CLOUD_HOST_IDLE_HOLD_S`, and releases hosts above it down to it. It also turns on rolling rebuilds; see below. |
| `CLOUD_POOL_MIN_FREE_SLOTS` | 4 | Demand mode only: when the free slots of the live hosts fall below this, the pool sweep adds a host ahead of demand. The default is one host's worth of sandboxes — the default host runs four — so the next host is under way once fewer than four slots are free; a deployment that creates a different host size sets this to that host's capacity. An idle host is not let go if that would take the free slots below it. |
| `CLOUD_HOST_IDLE_HOLD_S` | 1800 | How long a host that has no sandbox on it is kept before it is released. In fixed mode an idle host is not released unless the pool is over its size. |
| `CLOUD_POOL_MAX_HOSTS` | 12 | The platform's cap on hosts, whole cloud VMs among them, protecting the cluster: the current 400 GB shared thin pool holds ten hosts' disks written full, so each place above ten over-commits it by a disk. It rises only as the storage behind it grows. A session that finds the pool full and every host full is told capacity is tight and to try later. |
| `CLOUD_HOST_REBUILD_AFTER_DAYS` | 14 | Fixed mode only: a host older than this is rebuilt even on the current image (0 = age alone never rebuilds it). A host whose enrollment script, offering, template or size changed is rebuilt regardless of age. |
| `CLOUD_SANDBOX_CREDITS_PER_HOUR` | none | Credits a running sandbox costs per hour. Unset, no cloud sandbox starts. |
| `CLOUD_VM_CREDITS_PER_HOUR` | `{}` | Credits per hour of a VM sandbox, from creation to release, by its size as `<cores>c<GiB>g` (`4c8g`). A size not listed cannot be created. |

Hosts are VMs (`MICROCLOUD_OFFERING_ID`; on dev the same `standard-vm` offering as whole
VMs): a VM gives the host real root, which the per-sandbox disk limit below needs and an
unprivileged container does not have. Hosts are created at `MICROCLOUD_DEFAULT_CORES` /
`_MEMORY_MB` / `_DISK_GB` (4 cores, 16 GiB, 40 GB; clamped into the offering), with the data
disk the offering gives each of its machines (`dataDiskGb`); an offering that gives none
gives the pool no host. A host runs `cores × CLOUD_HOST_SLOTS_PER_CORE` sandboxes at once, but
no more than fit, each at its full `CLOUD_SANDBOX_MEMORY_MB`, in the memory it gives its
sandboxes (its memory less a quarter, at least 1 GiB), nor more than fit on its data disk at
`CLOUD_SANDBOX_DISK_GB` each after `CLOUD_HOST_DISK_RESERVE_GB` for its projects' package
stores: four on the default host with a 64 GB data disk. There is no team or project quota on
cloud. A running sandbox is charged in credits by the hour, from the same packs as model calls
(`docs/manual/dev/billing.md`, 云端算力).

### Hosts are rolled one at a time onto the current image

A host row records the image it was made of (`cloud_hosts.image_version`,
`enrollment.host_image_version`): a digest of the enrollment script, the offering and its
template, the login user and the size the machine gets — not one machine's credentials, so
two hosts of the same settings carry the same version. In fixed mode the sweep compares each
accepting ordinary host against `enrollment.current_host_image_version()`, and against
`CLOUD_HOST_REBUILD_AFTER_DAYS`. A host that differs, or that is older than the fallback
age, is rebuilt: it is marked draining (`cloud_hosts.draining_since`), takes no new sandbox,
and once its last sandbox is gone is released and deleted through the usual path; the next
sweep's `grow` puts a fresh host in its place with the current version. At most one host
drains at a time, and no host is drained while the others' free slots cannot hold what it
carries — a rebuild never interrupts a running sandbox, it only waits it out. Demand mode
does not rebuild (a host that empties is released and replaced by demand anyway).

### Each sandbox's disk has a limit of its own

A host enrolls only with its data disk (`enrollment.DATA_DISK`): the VM's `scsi1`, formatted
XFS the first time, mounted at `/data` with project quotas and `discard` (what a sandbox
deletes goes back to the thin pool under the VM), and bound over `~/.cheese/home` and
`~/.cheese/store`, where the homes and the package stores are. The size it found is the host's
`sandbox_disk_gb`. The same step makes the host's swap file on that disk, `/data/swapfile`
(`CLOUD_HOST_SWAP_MB`, 8 GiB; 0 makes none), with `fallocate` only — the disk is on a thin
pool, and a `dd`'d file would claim every block it wrote before anything trimmed it back —
`chmod 600`, `mkswap`, `swapon`, its fstab line (`swap,nofail`) and `vm.swappiness` in
`/etc/sysctl.d/90-cheese-swap.conf` (`CLOUD_HOST_SWAPPINESS`, 10). A host whose disk cannot
hold it does not enroll. The step is idempotent because enrollment is retried as a whole: a
file already swapped on is left alone (swapping it off to resize would push a host that is
short of memory into the case the file exists for), and one that is not swapped on but of
another size is made again. It buys the
machine room for a burst in its own processes (the connector, sshd, a language server) — a
slow page instead of the kernel killing them — and counts against the data disk in
`models.capacity`, so the sandbox count the host is given already leaves it out. A sandbox
itself still has no swap (`memory.swap.max` stays 0), which is what keeps the idle page-cache
reclaim safe. When a sandbox starts, `sandbox_host.py` gives its home a project quota of
`CLOUD_SANDBOX_DISK_BUDGET_GB` (12) and `CLOUD_SANDBOX_INODE_BUDGET` (600,000 inodes), its
executor's state (`~/.cheese/executor` in the home) a small one of its own, so a sandbox that
filled its home still answers and says so, and its project's store the same limits as a home.
A write past a limit is refused (ENOSPC) to that sandbox alone. Inside its sandbox a session
cannot change a file's project or take the inheritance off a directory: the kernel allows that
only from the machine's own user namespace (verified on a dev VM, 2026-10-20). A hard link
crosses neither the sandbox's mounts nor two projects; uv and pnpm clone from the store
instead (`UV_LINK_MODE=clone`, `npm_config_package_import_method=clone-or-copy`), which on
XFS shares the store's blocks. A sandbox whose home is not on such a disk does not start.

A sandbox that filled its own disk keeps its executor (`bootstrap.py`): a prepare that changes
nothing writes nothing, what the executor can do without (skills, the preview token, the
environment's config) is skipped and the tool call told how much is left, an upgrade whose
release cannot be staged waits as it waits for a busy executor, and a command that fails there
says `disk full: N MB free, M files left`. The session stays on its host and its room is told
the disk is full; another host would give it an empty home and none of its work.

### The disk as a whole, and idle memory

`backend/app/domain/machine/disk_guard.py`; the host side is `sandbox_home.py`'s `disk`. The
limits of a host's sandboxes add up to more than its disk, so every `SANDBOX_DISK_CHECK_S`
(60 s), every online machine holding a sandbox — the platform's hosts and people's computers
alike — and every platform host in the pool, holding one or not, reports the free bytes and
inodes of the filesystem its homes are on, and on a host what each home holds by its quota, a
`statvfs` each.

- Below `SANDBOX_DISK_FREE_FLOOR` (10%) of its bytes free or `SANDBOX_INODES_FREE_FLOOR` (5%)
  of its inodes, a host takes no new sandbox (`cloud_hosts.disk_full_since`) until a reading
  finds room again. Nothing on it is stopped: each sandbox is within its own limit.
- A sandbox's home with no sandbox any more — its row went when its session moved away — is
  deleted, ten minutes after it was last written. A home without the sandbox marker (a room's
  own directory on a computer that runs it without a sandbox) is never touched.
- A sandbox whose session has asked for no tool for two minutes gives back its page cache
  (`sandbox_host.py reclaim`, cgroup `memory.reclaim`); the guest frees it, and the VM's
  balloon reports free pages to the hypervisor, which takes them back (5.8 GiB down to
  1.4 GiB within 25 s, measured on a dev VM).
- An executor that cannot be installed because the disk is full says so rather than repeating
  the path the error named.

### What hosts and sandboxes use is shown and alerted

`backend/app/domain/machine/readings.py`. The same reading also carries a host's memory and
swap (`/proc/meminfo`), its memory pressure (`/proc/pressure/memory`: for `some` and `full`,
the share of the last minute and five the kernel waited on memory), whether zswap is loaded,
the CPU time of its cgroup root, its load, and each sandbox cgroup's `memory.current`,
`memory.peak`, `memory.stat`'s `anon` and `file`, `memory.swap.current` and
`memory.events`' `oom_kill`. The admin page draws the swap as a meter beside memory and says
the pressure; a sandbox that has used swap or been OOM-killed is flagged. A machine that
reports none of these — macOS has no PSI, and a host with no swap file — leaves them NULL
rather than a zero that reads like an idle swap. A platform host's reading is kept
(`host_readings`, six hours) with each sandbox's latest memory and disk (`sandbox_usages`), and
the admin page `/admin/machines` shows them beside the sandbox limits, with each reading's age.
A host past 85% of its disk, 90% of its inodes or 90% of its memory on two readings running is
sent to the alert group, keyed per host and resource, so the alert path's backoff (an hour,
four hours, a day) repeats it while it lasts. A person's computer's reading is not kept.

### Idle sandboxes are destroyed

`backend/app/domain/machine/lifecycle.py`; the host side is `machine/sandbox_home.py`.

- **Idle** is no turn running in the conversation the session works in and no tool asked
  for in `CLOUD_SANDBOX_IDLE_STOP_S`, counted from the later of the session's last tool call
  and the end of that conversation's last turn. A command the executor still runs for the
  session (a Bash call sent to the background; the executor reports `running_commands`)
  keeps it up, but only until `CLOUD_SANDBOX_BACKGROUND_CAP_S`. A process the agent detached
  itself, such as a dev server started with `nohup … &`, keeps nothing up.
- **Destroyed**: the executor is stopped, whatever still holds the home (a dev server) is
  ended, and the home is deleted from the host; then its row goes and its slot is free. The
  room's 现场 records 「环境 N 分钟没有活动，已释放……」. Nothing of the sandbox is kept:
  what lasts is the conversation and the commits the session pushed. The next tool call
  places the session in a new sandbox on any host with a free slot, which starts from the
  task branch like any new sandbox, and that call's result tells the agent once that it is in a new one. A tool call that arrives
  while the home is being deleted waits for it to go. A deletion that does not finish is
  done again by a later sweep three minutes on.
- A project whose credits ran out has its sandboxes destroyed as soon as its rooms run no
  turn, and the conversation is told so.

A host is released once it has had no sandbox on it for `CLOUD_HOST_IDLE_HOLD_S` — in fixed
mode only while the pool is over its size, since the size is what keeps a host up. A session
that switches away leaves its sandbox after one best-effort checkpoint (`cheese sync --all`),
whether or not the checkpoint went through; the sandbox it left is destroyed by the idle sweep
like any other, and holds its slot until then. A host the provider fails before it is enrolled
holds nothing of anyone's: the pool gives it up, deletes it, and places its sessions again;
after three such failures within an hour it stops creating hosts for the rest of the hour.

Home archives written before idle sandboxes were destroyed (`sandbox-archives/` in the
private bucket) are kept 30 days from the release that stopped archiving, listed in
`retained_home_archives`. Each conversation that has one is told once until when; past
that date the sandbox sweep deletes the object and its row (`machine/retained_archives.py`).
Until then an operator fetches one with `python -m scripts.retained_files homes`.

Sandboxes are disposable: what counts is what was pushed. So the pool also gives up on an
enrolled host the provider reports in `error`, and on one whose connector has been away for
ten minutes (`reaper.LOST_AFTER`, counted by the sweep in `cloud_hosts.offline_since`) while
another host of the pool is online. With none online, the sweep takes the outage for the
platform's own (a restart, a cut link) and gives up on nothing. Each session that worked on
the host is placed in a new sandbox on its next tool call; that call's result tells the agent,
once, that it is in a new sandbox, and the room hears 「环境不再响应，已换成新的……」. Only the `error` report counts toward the
three failures. VM sandboxes are not given up this way.

### VM sandboxes

A session's environment can be a sandbox of spec `vm` (`agent.environment`): the session
gets a virtual machine of its own, for work a bubblewrap sandbox cannot do —
Docker, KVM, kernel modules, root. It is a host row too (`CloudHost.whole_machine`), created
under the same platform customer and account from `MICROCLOUD_VM_OFFERING_ID`, enrolled the
same way, and counted against `CLOUD_POOL_MAX_HOSTS`. It is never taken from the warm pool,
no other session is placed on it, and the session's executor runs on it without a sandbox.
The room hears 「正在准备虚拟机沙箱」 and 「虚拟机沙箱已就绪」; the session sees the environment,
never the machine.

| Setting | Default | What it does |
|---|---|---|
| `MICROCLOUD_VM_OFFERING_ID` | 0 | The offering VMs are created from. 0: the deployment does not offer VM sandboxes, and the spec is not offered. |
| `CLOUD_VM_CORES` / `_MEMORY_MB` / `_DISK_GB` | 4 / 8192 / 40 | The one VM size, clamped into the offering. Recorded on the row with the project the VM was created for. |
| `CLOUD_VM_IDLE_RELEASE_S` | 1800 | A VM is pushed and released once its session has been idle this long, by the measure a sandbox is destroyed by (no turn in its conversation, no tool call, no background command still running). |
| `CLOUD_VM_BACKGROUND_CAP_S` | 3600 | How long a background command keeps a VM up after the session's last activity. Its own setting, not the sandbox's: the sandbox cap (900 s) is shorter than the VM's idle release, so it would hold nothing up. |

The pool sweep releases it as soon as no home is
left on it, with no idle hold: when its session switches away after pushing, when the room's
cleanup removed its directory, or when its session was idle (`machine/cloud_vm.py`, run by
the sandbox sweep). The idle release runs the same push as a switch first; a VM whose push
fails or cannot run is kept and asked again ten minutes later. A session that leaves
without pushing keeps its VM until its room's cleanup, like a home on a host. Releasing a
VM logs its spec, project and lifetime (`services._vm_released`), the point where it will
be charged.

A session has root on its VM, so no rule inside the VM can bound its network; MicroCloud
does that. It keeps a new VM off its private network: the MicroCloud API and console, the
servers there, Proxmox's internal address and other guests are unreachable, while the
internet, DNS, apt and the SSH reverse forward the connector's control link uses work.
Proxmox's public addresses (ports 8006 and 22) are still reachable from a VM.

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
waiting on a sandbox whose host is up. Destroying idle sandboxes and releasing idle VMs is a
second loop on the same interval (`SandboxSweeper`), so a VM's push that takes minutes does
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
this deployment (0 disables replenishment; maximum 5). It applies to a pool sized by demand
only: with `CLOUD_POOL_FIXED_HOSTS` above 0 the warm target reads as 0, the sweep deletes any
unused warm machine through the same path a size of 0 always did, and a host being replaced
in a rebuild is created directly — a warm machine would be one more machine running and
billed for nothing, and a host taken from it carries no image version. These machines use a
platform account and have no team binding. When the host pool needs another host it takes one: a
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
