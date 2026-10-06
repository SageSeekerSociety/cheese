/** 任务页头：只有负责人能开始、能换做它的电脑；别人看得到它在哪台电脑上做。做这件事的人都能改名。 */
import type { Component } from 'vue'
import type { RoomTask, Topic } from '@/cx_types'
import type { TopicComputeProfile } from '@/types/compute'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

let me = 'alice'
vi.mock('@/me', () => ({ myHandle: () => me }))
vi.mock('../../me', () => ({ myHandle: () => me }))
vi.mock('@/components/common/UserRefLink.vue', () => ({ default: { name: 'UserRef', template: '<span />' } }))

import TaskHeader from './TaskHeader.vue'

import { setLocale } from '@/i18n'

setLocale('zh-CN')
const vuetify = createVuetify({ components, directives })

const ROOM = { id: 'r1', project_id: 'p1', title: '前端', kind: 'topic', status: 'active' } as unknown as Topic

function task(over: Partial<RoomTask> = {}): RoomTask {
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
    ...over,
  } as RoomTask
}

const LAPTOP = { name: '王宁的笔记本', profile: 'device', device_id: 'd1', whole_machine: false }
const MACHINE = {
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
} as unknown as TopicComputeProfile

const PEOPLE = ['alice', 'bob', 'carol'].map((member_handle) => ({ member_handle, agent: false }))

function mount(over: Partial<RoomTask> = {}) {
  const start = vi.fn(async () => {})
  const loadMachine = vi.fn(async () => {})
  const setCollaborators = vi.fn(async () => true)
  const rename = vi.fn(async () => true)
  const view = render(TaskHeader as Component, {
    props: {
      room: ROOM,
      task: task(over),
      memberNames: {},
      agentName: '芝士',
      people: PEOPLE,
      machine: MACHINE,
      machineError: false,
      starting: false,
      startError: null,
      actionError: null,
      start,
      close: async () => true,
      handOver: async () => true,
      rename,
      setCollaborators,
      loadMachine,
    },
    global: { plugins: [vuetify] },
  })
  return { ...view, start, loadMachine, setCollaborators, rename }
}

function stubViewport() {
  // VMenu 要 visualViewport 和 devicePixelRatio，happy-dom 没有。
  vi.stubGlobal('devicePixelRatio', 1)
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
}

async function openDetails(container: Element) {
  stubViewport()
  await fireEvent.click(container.querySelector('[data-testid="task-details"]')!)
  await waitFor(() => expect(document.body.textContent).toContain('王宁的笔记本'))
}

beforeEach(() => {
  me = 'alice'
  document.body.innerHTML = ''
})

describe('任务页头', () => {
  it('负责人点「开始」就开始', async () => {
    const { container, start } = mount()
    const button = container.querySelector('[data-testid="task-start"]')
    expect(button).not.toBeNull()
    await fireEvent.click(button!)
    expect(start).toHaveBeenCalledWith(null)
  })

  it('开始之后不再有「开始」', () => {
    const { container } = mount({ started_at: '2026-10-05T01:00:00Z', started_by: 'alice' })
    expect(container.querySelector('[data-testid="task-start"]')).toBeNull()
  })

  it('不做这件事的人：不能开始，也没有更多操作', () => {
    me = 'bob'
    const { container } = mount()
    expect(container.querySelector('[data-testid="task-start"]')).toBeNull()
    expect(container.querySelector('[data-testid="task-more"]')).toBeNull()
  })

  it('负责人在任务信息里看得到工作电脑，也能更换', async () => {
    const { container, loadMachine } = mount()
    await openDetails(container)
    expect(loadMachine).toHaveBeenCalled()
    expect(document.querySelector('[data-testid="task-machine"] button')).not.toBeNull()
  })

  it('不是负责人：看得到任务在哪台电脑上做，不能更换', async () => {
    me = 'bob'
    const { container } = mount()
    await openDetails(container)
    const row = document.querySelector('[data-testid="task-machine"]')!
    expect(row.textContent).toContain('王宁的笔记本')
    expect(row.querySelector('button')).toBeNull()
  })

  it('负责人把名册上的人加为协作者', async () => {
    const { container, setCollaborators } = mount({ contributor_handles: ['bob'] })
    await openDetails(container)
    const row = document.querySelector('[data-testid="task-collaborators"]')!
    const pick = row.querySelector('select')!
    // 能加的只有还不在任务里的人：负责人自己和已经在协作的不在里面。
    expect([...pick.options].map((o) => o.value).filter(Boolean)).toEqual(['carol'])
    await fireEvent.update(pick, 'carol')
    const add = [...row.querySelectorAll('button')].find((b) => b.textContent?.includes('添加协作者'))!
    await fireEvent.click(add)
    expect(setCollaborators).toHaveBeenCalledWith(['bob', 'carol'])
  })

  it('协作者只能把自己去掉，不能加别人', async () => {
    me = 'bob'
    const { container, setCollaborators } = mount({ contributor_handles: ['bob', 'carol'] })
    await openDetails(container)
    const row = document.querySelector('[data-testid="task-collaborators"]')!
    expect(row.querySelector('select')).toBeNull()
    const buttons = [...row.querySelectorAll('button')]
    expect(buttons).toHaveLength(1)
    await fireEvent.click(buttons[0])
    expect(setCollaborators).toHaveBeenCalledWith(['carol'])
  })

  it('协作者能改名，不能转交和关闭', async () => {
    me = 'bob'
    const { container, rename } = mount({ contributor_handles: ['bob'] })
    stubViewport()
    await fireEvent.click(container.querySelector('[data-testid="task-more"]')!)
    await waitFor(() => expect(document.querySelector('[data-testid="task-rename"]')).not.toBeNull())
    expect(document.body.textContent).not.toContain('关闭任务')
    expect(document.body.textContent).not.toContain('转交')
    await fireEvent.click(document.querySelector('[data-testid="task-rename"]')!)
    const input = await waitFor(() => {
      const el = document.querySelector<HTMLInputElement>('input[aria-label="任务名称"]')
      expect(el).not.toBeNull()
      return el!
    })
    await fireEvent.update(input, '记住我')
    await fireEvent.keyDown(input, { key: 'Enter' })
    expect(rename).toHaveBeenCalledWith('记住我')
  })
})
