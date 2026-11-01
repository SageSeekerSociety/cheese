"""The machines a sandbox runs on: what one may use, and the pool of cloud hosts.

`config.py` is at the size cap every module is held to
(`.claude/scripts/check-file-sizes.py`, `.claude/rules/architecture.md`), and
this section stands alone: what one sandbox on a machine may use — its memory,
CPU, processes and disk — and everything the platform decides about the hosts
it rents: how one is sized, when the pool takes another, how long an idle
sandbox or a whole VM is kept. The product settings stay on `Settings`.
`Settings` subclasses the class below, so the fields, their environment names
and `settings.cloud_*` / `settings.sandbox_disk_*` / `settings.microcloud_*` are
exactly what `config.py` declared before the move: `backend/.env.example` is
held to `Settings.model_fields`, and a field declared anywhere on that class is
a field it reads.
"""

from pydantic import BaseModel, Field


class MachineSettings(BaseModel):
    # --- MicroCloud: the platform's pool of cloud hosts ---
    # Every cloud host belongs to the platform, under one MicroCloud customer and
    # account of its own, and carries the sandboxes of sessions from any project.
    # Cheese is one MicroCloud tenant and keeps the opaque provider secret
    # server-side. Empty secret = the feature reports itself unavailable without
    # breaking self-hosted compute.
    microcloud_base_url: str = ""
    microcloud_tenant_secret: str = ""
    microcloud_timeout_s: float = 30.0
    # Pin a specific granted offering (machine type + zone + template); 0 = take
    # the first active one, which is right while a tenant is granted exactly one.
    microcloud_offering_id: int = 0
    # The size every cloud host is created with. Every value is clamped into the
    # chosen offering's own range, so these are preferences, not guarantees.
    # Four sandboxes at their full 3 GiB limit fit in 16 GiB less the host's own
    # quarter (`models.capacity`); a 4 GiB host held one.
    microcloud_default_cores: int = 4
    microcloud_default_memory_mb: int = 16384
    microcloud_default_disk_gb: int = 40
    # Prepare the default CPU offering; zero disables replenishment.
    microcloud_warm_pool_size: int = Field(default=0, ge=0, le=5)
    # Requires deploy/cloud-control.py on the backend host before enrollment.
    microcloud_direct_control: bool = False
    microcloud_warm_max_age_seconds: int = Field(default=3600, ge=300, le=86400)
    microcloud_login_user: str = "cheese"
    # What one session's sandbox on a Cloud machine may use
    # (`remote_execution/sandbox_host.py`), whatever else shares the machine.
    # Memory: 3 GiB, a quarter of what the default 16 GiB host gives its
    # sandboxes. Sessions in this repository's
    # own earlier sandboxes had `pnpm run build` and `vue-tsc` OOM-killed at
    # 2 GiB (docs/topics), and the limit is there so that a session over it is
    # killed alone, not to share the machine out evenly. No swap of its own
    # (`cloud_sandbox_swap_mb` stays 0), so a session at its limit is killed
    # instead of pushing the machine into swap; the idle page-cache reclaim
    # (`sandbox_host.reclaim`) is only safe because nothing in a running
    # sandbox's cgroup can be swapped. A frozen sandbox is the exception: its
    # processes do not run, so it is allowed this much swap while it sleeps
    # (`machine/lifecycle.py`), so pages it will not touch may leave the
    # machine. The host itself keeps a swap file
    # (`cloud_host_swap_mb`), so a burst in the machine's own processes —
    # the connector, sshd, the release's language servers — is absorbed
    # instead of the kernel killing them. CPU:
    # two cores each, so a session alone on a host gets half of it; when they
    # all run, sessions share by equal weight. Processes: stops a
    # fork bomb, well above the threads a Node or JVM build starts.
    cloud_sandbox_memory_mb: int = Field(default=3072, ge=64)
    cloud_sandbox_swap_mb: int = Field(default=0, ge=0)
    cloud_sandbox_cpus: int = Field(default=2, ge=1, le=1024)
    cloud_sandbox_pids: int = Field(default=4096, ge=16)
    # Disk. On a platform host a sandbox's home — its checkout, its
    # dependencies, its /tmp — is on the host's data disk (`/data`, made when
    # the host enrolls: `enrollment.bootstrap_script`), shared with the
    # host's other sandboxes; on a person's computer, on their disk with
    # everything of theirs. Every machine holding a sandbox has its free bytes
    # and inodes read this often (`machine/disk_guard.py`); below these shares
    # free, of either, a platform host takes no new sandbox. The shares are
    # the defaults of Kubernetes' `nodefs.available` and `nodefs.inodesFree`.
    # Inodes are their own floor: on 2026-10-14 a dev host ran out of them
    # with 15 GB still free.
    sandbox_disk_check_s: int = Field(default=60, ge=10, le=3600)
    sandbox_disk_free_floor: float = Field(default=0.10, ge=0, le=0.9)
    sandbox_inodes_free_floor: float = Field(default=0.05, ge=0, le=0.9)
    # What one sandbox is expected to take of a host's data disk, for how many
    # a host runs at once (`models.capacity`), after `cloud_host_disk_reserve_gb`
    # for its projects' package stores. Measured on dev, 2026-10-14: a sandbox
    # with one task's checkout and its dependencies held 1.5–2.3 GB and
    # 95–130 thousand inodes; one project's package store held 6.5 GB.
    cloud_sandbox_disk_gb: int = Field(default=4, ge=1)
    cloud_host_disk_reserve_gb: int = Field(default=10, ge=0)
    # A swap file on a pool host's data disk, made when the host enrolls
    # (`enrollment.DATA_DISK`): 8 GiB, counted against the disk its sandboxes
    # share (`models.capacity`) and left out at 0. Extra room nothing is
    # expected to use in normal running — it turns a burst in the machine's
    # own processes into a slow page rather than an OOM kill of the connector
    # and every sandbox with it, and it is what lets a frozen sandbox's pages
    # leave RAM entirely (step after this one). A sandbox still has no swap
    # of its own.
    cloud_host_swap_mb: int = Field(default=8192, ge=0)
    # How eagerly the host swaps: the kernel's own default is 60, which
    # trades page cache away for anonymous memory all the time. 10 keeps it
    # to the case the file is for — memory actually short. Written to
    # /etc/sysctl.d/90-cheese-swap.conf beside the file.
    cloud_host_swappiness: int = Field(default=10, ge=0, le=100)
    # What one sandbox on a platform host may hold, however much room the rest
    # of the disk has: its home's project quota (`sandbox_host.py`), past
    # which its writes are refused (ENOSPC). Each project's package store on
    # the host has the same limit of its own. About five times the
    # measurement above. Not on a person's computer, whose disk is theirs to
    # fill; there only the floors above are read.
    cloud_sandbox_disk_budget_gb: int = Field(default=12, ge=1)
    cloud_sandbox_inode_budget: int = Field(default=600_000, ge=1000)
    # An operator's SSH public key, authorised on every machine the platform
    # opens, next to the one-shot bootstrap key. That key is erased the moment
    # enrollment succeeds, so without this nobody can read a Cloud machine's
    # connector journal afterwards — which is why the 2026-08-29 failure on
    # machine 477 was never diagnosed. Platform-provisioned machines only: a
    # self-hosted box is someone else's and never gets a key of ours.
    microcloud_operator_ssh_pubkey: str = ""
    # The platform's fund account for its hosts, and the balance kept in it.
    # MicroCloud bills compute against this; 0 disables top-ups (an operator funds
    # it by hand).
    microcloud_account_name: str = "compute"
    microcloud_initial_funds: float = 1000.0
    # Sandbox slots per host core: how many sessions' sandboxes one host runs at
    # once, unless its memory holds fewer at `cloud_sandbox_memory_mb` each
    # (`models.capacity`). A sandbox holds its slot for as long as it exists.
    cloud_host_slots_per_core: int = Field(default=2, ge=1, le=16)
    # A cloud sandbox is destroyed once its conversation has run no turn and
    # its session has asked for no tool for this long; the next tool call gets
    # a new one (`machine/lifecycle.py`).
    cloud_sandbox_idle_stop_s: int = Field(default=600, ge=60, le=86400)
    # When the pool is pressed — no host that may take a sandbox has a free
    # slot, or a session is waiting on its sandbox — an idle sandbox on a
    # platform host is destroyed after this much instead, freeing its slot for
    # the session waiting on it (`machine/lifecycle.py`). A person's own
    # computer keeps the idle time above, and a whole cloud VM is released on
    # its own clock (`cloud_vm_idle_release_s`).
    cloud_sandbox_idle_stop_pressed_s: int = Field(default=180, ge=1, le=86400)
    # An idle cloud sandbox is frozen instead of destroyed: its processes are
    # stopped (`cgroup.freeze`), whatever of its memory the kernel can take is
    # given back, and its home, its work and its executor wait on the host
    # (`machine/lifecycle.py`). The session's next tool call wakes the same
    # sandbox. Off, an idle sandbox is destroyed as it always was. Only a
    # standard sandbox on a platform host is frozen; a whole cloud VM and a
    # sandbox on somebody's own computer keep the old lifecycle.
    cloud_sandbox_freeze: bool = True
    # How long a frozen sandbox is kept before it is destroyed anyway: it holds
    # its host's slot and its home the whole time, so it cannot be kept for
    # good. A slot needed, a disk running short or a host being rebuilt take a
    # frozen sandbox sooner (`machine/lifecycle.py`, `disk_guard`, `reaper`).
    cloud_sandbox_frozen_keep_s: int = Field(default=7200, ge=60, le=30 * 86400)
    # A command the executor still runs for the session (a Bash call sent to the
    # background) keeps an otherwise idle sandbox up, but no longer than this
    # after the session's last activity.
    cloud_sandbox_background_cap_s: int = Field(default=900, ge=0, le=7 * 86400)
    # Pre-scale: when the free slots of the pool's live hosts fall below this,
    # the pool sweep claims (or creates) the next host before anyone waits on it.
    # The default host (four cores, 16 GiB, 40 GB) runs four sandboxes, so the
    # floor is one host's worth: the next host is under way once fewer than four
    # slots are free, instead of only once the pool is full. A deployment that
    # creates a different host size sets this to that host's capacity.
    cloud_pool_min_free_slots: int = Field(default=4, ge=0, le=64)
    # How long a host that has no sandbox on it is kept before it is released.
    cloud_host_idle_hold_s: int = Field(default=1800, ge=0, le=86400)
    # The most hosts the pool holds at once, whole cloud VMs among them. It
    # protects the MicroCloud cluster: the current 400 GB shared thin pool holds
    # ten hosts' disks written full, so each place above ten over-commits it by
    # a disk. Raise this only as that pool grows.
    cloud_pool_max_hosts: int = Field(default=12, ge=1, le=500)
    # How many ordinary hosts the pool keeps up, whatever the demand: ten, the
    # number the shared thin pool above holds, leaving the other two places for
    # whole cloud VMs. One is added until there are this many, an idle one is
    # kept instead of released after `cloud_host_idle_hold_s`, and one above
    # this number is released once it is idle. 0 = no fixed size: the pool is
    # sized by demand (`cloud_pool_min_free_slots`) and an idle host is released
    # again, which is what the platform did before this setting.
    cloud_pool_fixed_hosts: int = Field(default=10, ge=0, le=500)
    # A host older than this is rolled: taken out of rotation (it takes no new
    # sandbox, and is released once its last one is gone) and replaced with a
    # fresh one, so the fleet takes new enrollment scripts and templates without
    # an outage. A host whose image version is no longer the current one is
    # rolled regardless of age. 0 = age alone never rolls a host. Rolling needs
    # a fixed size above (`cloud_pool_fixed_hosts`), and only one host is rolled
    # at a time.
    cloud_host_rebuild_after_days: int = Field(default=14, ge=0, le=365)
    # --- Whole cloud VMs: one session, one whole virtual machine (#2320) ---
    # The offering a session that asks for a whole machine gets its VM from
    # (Docker, KVM, kernel modules, root). 0 = this deployment does not offer
    # the choice. A VM counts against `cloud_pool_max_hosts` like a host.
    microcloud_vm_offering_id: int = 0
    # The size every whole cloud VM is created with, clamped into the offering's
    # own range. One size: what a session is charged is spec × time, and the
    # spec is recorded on the VM's row.
    cloud_vm_cores: int = Field(default=4, ge=1)
    cloud_vm_memory_mb: int = Field(default=8192, ge=1024)
    cloud_vm_disk_gb: int = Field(default=40, ge=10)
    # A session's VM is released once its room has run no turn and the session
    # has asked for no tool for this long, after its work is pushed. The next
    # tool call prepares a new one, which takes minutes, so this is longer
    # than a sandbox's idle stop.
    cloud_vm_idle_release_s: int = Field(default=1800, ge=60, le=7 * 86400)
    # A command the executor still runs for the session keeps an idle VM up,
    # but no longer than this after its last activity — longer than a
    # sandbox's cap, since a VM's idle release is longer too.
    cloud_vm_background_cap_s: int = Field(default=3600, ge=0, le=7 * 86400)
    # Credits one cloud sandbox costs per hour it runs, from start to idle stop
    # (usage/compute.py). Unset on purpose: the price is the product owner's to
    # set, and with none set no cloud sandbox starts, so cloud compute never
    # runs free by accident. 0 is an explicit "free".
    cloud_sandbox_credits_per_hour: float | None = Field(default=None, ge=0)
    # Credits per hour of a whole cloud VM, by its size as cores and GiB of
    # memory, e.g. `{"4c8g": 30}` (usage.compute.vm_spec). A size not named
    # here cannot be started.
    cloud_vm_credits_per_hour: dict[str, float] = Field(default_factory=dict)
    # How long a SETTLED machine may go without being re-checked against
    # MicroCloud by the sweep. Never would let a machine destroyed upstream sit
    # here as `running` forever (which happened, and also consumed the
    # per-project limit).
    microcloud_reconcile_interval_s: float = 120.0
    # How often to sweep for machines that came up and still need enrolling as
    # devices. Ten seconds, not sixty: a Cloud topic's first turn waits on this
    # clock, and at 60s a person waited up to a minute on a timer for a machine
    # that was already there. A tick with nothing unsettled is three cheap
    # queries.
    machine_enroll_interval_seconds: int = 10
