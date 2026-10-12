/**
 * 机器这一屏：provider 连续失败、平台在拒绝新会话时，页面要说出来；点开一台宿主机就看见
 * 它上面的沙箱；接口读失败不画成「一台宿主机都没有」。
 */
import type { MachineHost, MachinesOverview } from '@/views/admin/machinesApi'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
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
    enroll_attempts: 1,
    enroll_error: null,
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
      },
    ],
    ...fields,
  }
}

function overview(pool: Partial<MachinesOverview['pool']> = {}, hosts = [host()]): MachinesOverview {
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
      warm_target: 0,
      ...pool,
    },
    config: {
      slots_per_core: 2,
      sandbox_memory_mb: 3072,
      sandbox_idle_s: 600,
      host_idle_hold_s: 1800,
      vm_idle_s: 1800,
    },
    hosts,
    warm: [],
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
    const refusing = /Refusing new sessions now|此刻在拒绝新会话/
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

  it('读失败时不说「一台宿主机都没有」', () => {
    const none = i18n.global.t('admin.machines.noHosts')
    const empty = mount({ overview: overview({}, []) })
    expect(empty.queryByText(none)).not.toBeNull()
    empty.unmount()
    expect(mount({ overview: null, failed: true }).queryByText(none)).toBeNull()
  })
})
