/**
 * 机器这一屏：provider 连续失败、平台在拒绝新会话时，页面要说出来；点开一台宿主机就看见
 * 它上面的沙箱；接口读失败不画成「一台宿主机都没有」；每台宿主机用了多少内存、读数多旧
 * 看得见，旧到不能当此刻看的要说出来；每个沙箱的内存和它的上限放在一起。
 */
import type { HostUsage, MachineHost, MachinesOverview, WarmMachineRow } from '@/views/admin/machinesApi'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, within } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import AdminMachinesPageView from './AdminMachinesPageView.vue'

import i18n from '@/i18n'

const NOW = '2026-10-12T10:00:00Z'

function host(fields: Partial<MachineHost> = {}): MachineHost {
  return {
    id: 'h1',
    hostname: 'pool-a',
    machine_id: 12,
    whole_machine: false,
    project: null,
    phase: 'ready',
    status: 'running',
    online: true,
    ip: null,
    cores: 4,
    memory_mb: 16384,
    disk_gb: 40,
    slots: 5,
    used: 1,
    created_at: '2026-10-12T08:00:00Z',
    enrolled_at: '2026-10-12T08:02:00Z',
    last_seen_at: NOW,
    idle_since: null,
    offline_since: null,
    failed_at: null,
    released_at: null,
    image_version: null,
    image_outdated: null,
    draining: false,
    draining_since: null,
    enroll_attempts: 1,
    enroll_error: null,
    usage: null,
    sandboxes: [
      {
        id: 's1',
        spec: 'standard',
        state: 'working',
        project_id: 'p1',
        project: 'Alpha',
        room_id: 'r1',
        room: 'Launch room',
        placed_at: '2026-10-12T09:00:00Z',
        waiting_since: null,
        active_at: '2026-10-12T09:59:30Z',
        stopped_at: null,
        idle_deadline: null,
        usage: null,
      },
    ],
    ...fields,
  }
}

function warm(fields: Partial<WarmMachineRow> = {}): WarmMachineRow {
  return {
    id: 'w1',
    state: 'ready',
    machine_id: 88,
    hostname: 'warm-spare',
    cores: 4,
    memory_mb: 16384,
    disk_gb: 40,
    ip: null,
    online: true,
    created_at: '2026-10-12T09:30:00Z',
    enrolled_at: '2026-10-12T09:32:00Z',
    attempts: 0,
    error: null,
    ...fields,
  }
}

function overview(
  pool: Partial<MachinesOverview['pool']> = {},
  hosts = [host()],
  warmRows: WarmMachineRow[] = []
): MachinesOverview {
  return {
    configured: true,
    now: NOW,
    pool: {
      hosts: hosts.length,
      vms: 0,
      max_hosts: 10,
      at_cap: false,
      slots: 5,
      used: 1,
      free: 4,
      min_free: 2,
      waiting: 0,
      oldest_wait_since: null,
      provider_failures: 0,
      provider_failure_limit: 3,
      provider_window_min: 60,
      provider_probe_min: 5,
      last_provider_failure: null,
      refusing: false,
      fixed_hosts: 0,
      warm_target: 0,
      ...pool,
    },
    config: {
      slots_per_core: 2,
      sandbox_memory_mb: 3072,
      sandbox_idle_s: 600,
      host_idle_hold_s: 1800,
      rebuild_after_days: 14,
      vm_idle_s: 1800,
      sandbox_disk_budget_gb: 12,
      sandbox_inode_budget: 600000,
      near_cap: 0.8,
      check_s: 60,
      alert_used: { disk: 0.85, inodes: 0.9, memory: 0.9 },
      history_step_s: 300,
    },
    hosts,
    warm: warmRows,
    history: {
      since: '2026-10-11T10:00:00Z',
      prepared: 0,
      ready: 0,
      abandoned: 0,
      replaced: 0,
      released_idle: 0,
      released_unpaid: 0,
      wait_p50_s: null,
      wait_p95_s: null,
      wait_max_s: null,
      refused_sessions: 0,
      refused_by_reason: {},
      hosts_lost: 0,
      hosts_lost_by_reason: {},
      sandboxes_lost: 0,
    },
  }
}

const GIB = 2 ** 30

function usage(fields: Partial<HostUsage> = {}): HostUsage {
  return {
    read_at: '2026-10-12T09:59:30Z',
    disk_bytes: 40e9,
    disk_used_bytes: 10e9,
    inodes: 2_600_000,
    inodes_used: 300_000,
    memory_bytes: 16 * GIB,
    memory_used_bytes: 6 * GIB,
    swap_bytes: 8 * GIB,
    swap_used_bytes: 0.5 * GIB,
    memory_pressure_some_avg10: 1.5,
    memory_pressure_some_avg60: 2.5,
    memory_pressure_full_avg10: 0.2,
    memory_pressure_full_avg60: 0.1,
    zswap: false,
    cpu_busy: 0.25,
    load1: 1.2,
    sandboxes: 1,
    memory_history: [0.3, 0.35, null, 0.375],
    cpu_history: [0.1, 0.2, null, 0.25],
    ...fields,
  }
}

function mount(props: Record<string, unknown>) {
  return render(AdminMachinesPageView, {
    props,
    global: {
      plugins: [createVuetify({ components, directives }), i18n],
      stubs: { AppPage: { template: '<div><slot name="extra" /><slot /></div>' } },
    },
  })
}

describe('机器', () => {
  it('provider 连续失败、平台在拒绝新会话时，页面说出来', () => {
    const refusing = /Not starting new AI teammates now|此刻不再接新的 AI 队友/
    const calm = mount({ overview: overview() })
    expect(calm.queryByText(refusing)).toBeNull()
    calm.unmount()
    const bad = mount({
      overview: overview({ provider_failures: 3, refusing: true, last_provider_failure: '2026-10-12T09:58:00Z' }),
    })
    expect(bad.queryByText(refusing)).not.toBeNull()
  })

  it('点开一台宿主机，看见它上面的沙箱', async () => {
    const { getByText, queryByText } = mount({ overview: overview() })
    expect(queryByText(/Launch room/)).toBeNull()
    await fireEvent.click(getByText('pool-a'))
    expect(queryByText(/Launch room/)).not.toBeNull()
  })

  it('个人芝士对话的沙箱只写「个人对话」，不链到任何项目', async () => {
    const [mine] = host().sandboxes
    const personal = host({ sandboxes: [{ ...mine, project_id: null, project: null, room: null }] })
    const { getByText, queryByRole } = mount({ overview: overview({}, [personal]) })
    await fireEvent.click(getByText('pool-a'))
    const named = i18n.global.t('admin.machines.personal')
    expect(getByText(named).tagName).not.toBe('A')
    expect(queryByRole('link', { name: named })).toBeNull()
  })

  it('宿主机数和上限放在一起比时，整机虚拟机也算进去：上限就是这样数的', () => {
    const { getByText } = mount({ overview: overview({ hosts: 1, vms: 3, max_hosts: 4, at_cap: true }) })
    expect(getByText('4 / 4')).toBeTruthy()
  })

  it('读失败时不说「一台宿主机都没有」', () => {
    const none = i18n.global.t('admin.machines.noHosts')
    const empty = mount({ overview: overview({}, []) })
    expect(empty.queryByText(none)).not.toBeNull()
    empty.unmount()
    expect(mount({ overview: null, failed: true }).queryByText(none)).toBeNull()
  })

  it('每台宿主机的内存用了多少、对总量多少，读数多久前读到', () => {
    const { getByText, queryByText } = mount({ overview: overview({}, [host({ usage: usage() })]) })
    expect(getByText('6.0 / 16.0 GiB')).toBeTruthy()
    expect(queryByText(/Stale|已过期/)).toBeNull()
  })

  it('读数旧过三次巡检，页面说它过期了，不当此刻的数', () => {
    const old = usage({ read_at: '2026-10-12T09:50:00Z' })
    const { queryByText } = mount({ overview: overview({}, [host({ usage: old })]) })
    expect(queryByText(/Stale|已过期/)).not.toBeNull()
  })

  it('点开宿主机，每个沙箱的内存和它的 3 GiB 上限放在一起', async () => {
    const sandbox = {
      ...host().sandboxes[0],
      usage: {
        memory_bytes: 2.7 * GIB,
        memory_peak_bytes: 2.9 * GIB,
        anon_bytes: 2.4 * GIB,
        file_bytes: 0.3 * GIB,
        swap_bytes: 0,
        oom_kills: 0,
        memory_read_at: '2026-10-12T09:59:30Z',
        disk_bytes: 4e9,
        disk_inodes: 120_000,
        disk_read_at: '2026-10-12T09:57:00Z',
      },
    }
    const { getByText, queryByText } = mount({
      overview: overview({}, [host({ usage: usage(), sandboxes: [sandbox] })]),
    })
    await fireEvent.click(getByText('pool-a'))
    expect(queryByText(/2\.7 \/ 3\.0 GiB/)).not.toBeNull()
    expect(queryByText(/4\.0 \/ 12 GB/)).not.toBeNull()
  })

  it('宿主机用了多少交换文件、内存有多紧，页面都说出来', () => {
    const { getByText } = mount({ overview: overview({}, [host({ usage: usage() })]) })
    // 交换那一根条，和内存那根分开。
    expect(getByText('0.5 / 8.0 GiB')).toBeTruthy()
    expect(
      getByText(
        /Memory pressure \(1 min \/ 5 min\): some 1\.5% \/ 2\.5%, full 0\.2% \/ 0\.1%|内存压力（1 分钟 \/ 5 分钟）：some 1\.5% \/ 2\.5%，full 0\.2% \/ 0\.1%/
      )
    ).toBeTruthy()
  })

  it('沙箱用了交换文件、被 OOM 杀过，页面把它们标出来', async () => {
    const sandbox = {
      ...host().sandboxes[0],
      usage: {
        memory_bytes: 2.7 * GIB,
        memory_peak_bytes: 2.9 * GIB,
        anon_bytes: 2.4 * GIB,
        file_bytes: 0.3 * GIB,
        swap_bytes: 0.5 * GIB,
        oom_kills: 2,
        memory_read_at: '2026-10-12T09:59:30Z',
        disk_bytes: 4e9,
        disk_inodes: 120_000,
        disk_read_at: '2026-10-12T09:57:00Z',
      },
    }
    const { getByText, queryByText } = mount({
      overview: overview({}, [host({ usage: usage(), sandboxes: [sandbox] })]),
    })
    await fireEvent.click(getByText('pool-a'))
    expect(queryByText(/swap 0\.5 GiB|交换 0\.5 GiB/)).not.toBeNull()
    expect(queryByText(/OOM-killed 2 times|被 OOM 杀过 2 次/)).not.toBeNull()
  })

  it('还没被领走的温机也在宿主机表里，带着它此刻的阶段：在跑的机器一台都不漏', () => {
    const { getByRole } = mount({
      overview: overview({}, [host()], [warm(), warm({ id: 'w2', state: 'preparing', hostname: 'warm-booting' })]),
    })
    const table = within(getByRole('table'))
    const spare = table.getByText('warm-spare').closest('[role="row"]') as HTMLElement
    expect(within(spare).queryByText(/Unclaimed|待领用/)).not.toBeNull()
    const booting = table.getByText('warm-booting').closest('[role="row"]') as HTMLElement
    expect(within(booting).queryByText(/Warming up|预热中/)).not.toBeNull()
  })

  it('滚动重建的宿主机标出「重建中」，版本旧了的标出「镜像过期」', () => {
    const draining = i18n.global.t('admin.machines.draining')
    const outdated = i18n.global.t('admin.machines.outdated')
    const { getByText, queryByText } = mount({
      overview: overview({ fixed_hosts: 2 }, [
        host({ draining: true, draining_since: '2026-10-12T09:50:00Z', image_outdated: true }),
        host({ id: 'h2', hostname: 'pool-b', image_outdated: true }),
        host({ id: 'h3', hostname: 'pool-c' }),
      ]),
    })
    const rows = getByText('pool-a').closest('[role="row"]') as HTMLElement
    expect(within(rows).queryByText(draining)).not.toBeNull()
    const stale = getByText('pool-b').closest('[role="row"]') as HTMLElement
    expect(within(stale).queryByText(outdated)).not.toBeNull()
    const fresh = getByText('pool-c').closest('[role="row"]') as HTMLElement
    expect(within(fresh).queryByText(outdated)).toBeNull()
    expect(queryByText(i18n.global.t('admin.machines.fixedFoot', { n: 2, days: 14 }))).not.toBeNull()
  })

  it('只有温机、还没有宿主机时，不说「一台宿主机都没有」', () => {
    const none = i18n.global.t('admin.machines.noHosts')
    const { queryByText } = mount({ overview: overview({}, [], [warm()]) })
    expect(queryByText(none)).toBeNull()
    expect(queryByText('warm-spare')).not.toBeNull()
  })
})
