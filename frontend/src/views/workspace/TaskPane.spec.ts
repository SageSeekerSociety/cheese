/** 任务页：负责人能在任务里说话、能开始；别人看得到对话，说话的地方换成回房间的入口。 */
import type { Component } from 'vue'
import type { RoomTask, Topic } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getTask = vi.fn()
vi.mock('@/api/tasks', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/tasks')>()),
  getTask: (...a: unknown[]) => getTask(...a),
}))
const getTopicComputeProfile = vi.fn()
vi.mock('@/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api')>()),
  getTopicComputeProfile: (...a: unknown[]) => getTopicComputeProfile(...a),
}))
let me = 'alice'
vi.mock('@/me', () => ({ myHandle: () => me }))
vi.mock('../../me', () => ({ myHandle: () => me }))
vi.mock('@/components/room/composables/useRoomSocket', async () => {
  const { ref } = await import('vue')
  return {
    useRoomSocket: () => ({ open: vi.fn(), close: vi.fn(), connectRefused: ref(false), isConnectRefusal: () => false }),
  }
})
vi.mock('@/components/panels/PanelDoc.vue', () => ({ default: { name: 'PanelDoc', template: '<div />' } }))
vi.mock('@/components/panels/PanelChanges.vue', () => ({ default: { name: 'PanelChanges', template: '<div />' } }))
vi.mock('@/components/TopicAcceptCard.vue', () => ({ default: { name: 'TopicAcceptCard', template: '<div />' } }))
vi.mock('@/components/common/UserRefLink.vue', () => ({ default: { name: 'UserRef', template: '<span />' } }))

import TaskPane from './TaskPane.vue'

import { setLocale } from '@/i18n'

setLocale('zh-CN')
const vuetify = createVuetify({ components, directives })

const ROOM = { id: 'r1', project_id: 'p1', title: '前端', kind: 'topic', status: 'active' } as unknown as Topic

function task(over: Partial<RoomTask> = {}): RoomTask & { blocks: [] } {
  return {
    id: 't1',
    project_id: 'p1',
    room_id: 'r1',
    title: '登录页加「记住我」',
    title_source: 'human',
    status: 'open',
    owner_handle: 'alice',
    started_at: null,
    created_at: '2026-10-05T00:00:00Z',
    updated_at: '2026-10-05T00:00:00Z',
    presentation: { column: 'not_started', phrase: 'discussing' },
    blocks: [],
    ...over,
  } as RoomTask & { blocks: [] }
}

function mount() {
  return render(TaskPane as Component, {
    props: {
      room: ROOM,
      taskId: 't1',
      members: [],
      roomMembers: [],
      memberNames: {},
      agentName: '芝士',
      agentHandle: null,
      topicList: [],
    },
    global: { plugins: [vuetify] },
  })
}

beforeEach(() => {
  getTask.mockReset()
  getTopicComputeProfile.mockReset()
  me = 'alice'
})

const LAPTOP = { name: '王宁的笔记本', profile: 'device', device_id: 'd1', whole_machine: false }

function machine() {
  return {
    choice: LAPTOP,
    project_default: LAPTOP,
    current: 'device',
    device_id: 'd1',
    devices: [{ device_id: 'd1', name: '王宁的笔记本', online: true, owned: true, sandbox_unavailable: null }],
    sessions: [],
    profiles: [],
    cloud_vm_available: false,
    visibility: { options: [], effective: null, machine_access: false },
    follows_room: true,
  }
}

async function openDetails(container: Element) {
  // 任务信息是一个 VMenu，而 happy-dom 没有 visualViewport 和 devicePixelRatio。
  vi.stubGlobal('devicePixelRatio', 1)
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
  await waitFor(() => expect(container.querySelector('[data-testid="task-details"]')).not.toBeNull())
  await fireEvent.click(container.querySelector('[data-testid="task-details"]')!)
  await waitFor(() => expect(document.body.textContent).toContain('王宁的笔记本'))
}

describe('任务页', () => {
  it('负责人看得到输入框和「开始」', async () => {
    getTask.mockResolvedValue(task())
    const { container } = mount()
    await waitFor(() => expect(container.querySelector('[data-testid="task-composer"]')).not.toBeNull())
    expect(container.querySelector('[data-testid="task-start"]')).not.toBeNull()
  })

  it('开始之后不再有「开始」', async () => {
    getTask.mockResolvedValue(task({ started_at: '2026-10-05T01:00:00Z', started_by: 'alice' }))
    const { container } = mount()
    await waitFor(() => expect(container.querySelector('[data-testid="task-composer"]')).not.toBeNull())
    expect(container.querySelector('[data-testid="task-start"]')).toBeNull()
  })

  it('不是负责人：没有输入框、不能开始，只有回到房间的入口', async () => {
    me = 'bob'
    getTask.mockResolvedValue(task())
    const { container } = mount()
    await waitFor(() => expect(container.querySelector('[data-testid="task-blocked"]')).not.toBeNull())
    expect(container.querySelector('[data-testid="task-composer"]')).toBeNull()
    expect(container.querySelector('[data-testid="task-start"]')).toBeNull()
  })

  it('负责人在任务信息里看得到工作电脑，也能更换', async () => {
    getTask.mockResolvedValue(task())
    getTopicComputeProfile.mockResolvedValue(machine())
    const { container } = mount()
    await openDetails(container)
    expect(getTopicComputeProfile).toHaveBeenCalledWith('t1')
    const row = document.querySelector('[data-testid="task-machine"]')!
    expect(row.querySelector('button')).not.toBeNull()
  })

  it('不是负责人：看得到任务在哪台电脑上做，不能更换', async () => {
    me = 'bob'
    getTask.mockResolvedValue(task())
    getTopicComputeProfile.mockResolvedValue(machine())
    const { container } = mount()
    await openDetails(container)
    const row = document.querySelector('[data-testid="task-machine"]')!
    expect(row.textContent).toContain('王宁的笔记本')
    expect(row.querySelector('button')).toBeNull()
  })
})
