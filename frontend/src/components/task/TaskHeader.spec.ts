/** 任务页头：只有负责人能开始、能换做它的电脑、能换做它的队友；别人看得到它在哪台电脑上、交给了哪位队友。做这件事的人都能改名。 */
import type { Component } from 'vue'
import type { RoomTask, Topic } from '@/cx_types'
import type { TopicComputeProfile } from '@/types/compute'

import { nextTick } from 'vue'
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

/** 这间房名册上的 AI 队友：负责人能从这里面挑一位做这件事。 */
const AGENTS = [
  { member_handle: 'cheese-01', agent: true },
  { member_handle: 'cheese-02', agent: true },
]
const AGENT_NAMES = { 'cheese-01': '小苔', 'cheese-02': '无言' }

/** 名册这一行带着每个人**自己挑过的**头像：alice 挑了一张，bob 从来没挑过（null）。 */
const PICKED = [
  { member_handle: 'alice', agent: false, avatar_id: 7 },
  { member_handle: 'bob', agent: false, avatar_id: null },
  { member_handle: 'carol', agent: false, avatar_id: 9 },
]

function mount(over: Partial<RoomTask> = {}, people = PEOPLE, agents = AGENTS) {
  const start = vi.fn(async () => {})
  const loadMachine = vi.fn(async () => {})
  const setCollaborators = vi.fn(async () => true)
  const setAgent = vi.fn(async () => true)
  const rename = vi.fn(async () => true)
  const reopen = vi.fn(async () => true)
  const view = render(TaskHeader as Component, {
    props: {
      room: ROOM,
      task: task(over),
      memberNames: AGENT_NAMES,
      agentName: '芝士',
      people,
      agents,
      machine: MACHINE,
      machineError: false,
      starting: false,
      startError: null,
      actionError: null,
      start,
      close: async () => true,
      reopen,
      handOver: async () => true,
      rename,
      setCollaborators,
      setAgent,
      loadMachine,
    },
    global: { plugins: [vuetify] },
  })
  return { ...view, start, loadMachine, setCollaborators, setAgent, rename, reopen }
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

async function openAgentMenu(container: Element) {
  await openDetails(container)
  await fireEvent.click(document.querySelector('[data-testid="task-agent-edit"]')!)
  await waitFor(() => expect(document.querySelector('[data-testid="task-agent-menu"]')).not.toBeNull())
}

/** 换队友那张菜单里的每一行，从上到下：先是「跟随频道」，再是能挑的队友。 */
function agentRows(): HTMLElement[] {
  return [...document.querySelectorAll<HTMLElement>('.task-agent-menu__row')]
}

/** 菜单开着没有。收起的菜单连同它的卡片留在 DOM 里（Vuetify 只把它藏起来），
 * 所以看那颗「改」上的 aria-expanded，而不是看卡片在不在。 */
function agentMenuOpen(): boolean {
  return document.querySelector('[data-testid="task-agent-edit"]')?.getAttribute('aria-expanded') === 'true'
}

beforeEach(() => {
  me = 'alice'
  document.body.innerHTML = ''
})

describe('任务页头', () => {
  // 页头那一句和频道里的卡、全部任务、总览说同一件事：在等的是看的人，就写「待你开始」。
  it('等的是看的人：写「待你开始」，等的是别人：写「讨论中」', () => {
    me = 'alice'
    const mine = mount({ waiting_on: 'alice' })
    expect(mine.getByTestId('task-phrase').textContent).toBe('待你开始')
    mine.unmount()

    const theirs = mount({ owner_handle: 'bob', waiting_on: 'bob' })
    expect(theirs.getByTestId('task-phrase').textContent).toBe('讨论中')
  })

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

  it('负责人能换做这件事的队友，换完菜单就收', async () => {
    const { container, setAgent } = mount()
    await openAgentMenu(container)
    // 第一行是跟着房间的那位；能挑的是这间房名册上的队友，别人不在里面。
    expect(agentRows().map((r) => r.textContent?.trim())).toEqual(['跟随频道', '小苔', '无言'])
    await fireEvent.click(agentRows()[2])
    expect(setAgent).toHaveBeenCalledWith('cheese-02')
    await waitFor(() => expect(agentMenuOpen()).toBe(false))
  })

  it('换回「跟随频道」就是不给这件事单独指定队友', async () => {
    const { container, setAgent } = mount({ agent_handle: 'cheese-02' })
    await openAgentMenu(container)
    // 单独指定过的那一位排在「跟随频道」后面，是当前选中的那一行。
    expect(agentRows()[1].textContent?.trim()).toBe('无言')
    expect(agentRows()[0].querySelector('.mdi-radiobox-marked')).toBeNull()
    expect(agentRows()[1].querySelector('.mdi-radiobox-marked')).not.toBeNull()
    await fireEvent.click(agentRows()[0])
    expect(setAgent).toHaveBeenCalledWith(null)
  })

  it('没单独指定时，负责人看到的是房间的那位，也写明跟着频道', async () => {
    const { container } = mount()
    await openDetails(container)
    const row = document.querySelector('[data-testid="task-agent"]')!
    expect(row.textContent).toContain('芝士')
    expect(row.textContent).toContain('跟随频道')
    expect(row.querySelector('[data-testid="task-agent-edit"]')).not.toBeNull()
  })

  it('不是负责人：没单独指定时，写明这位是跟着频道的', async () => {
    me = 'bob'
    const { container } = mount()
    await openDetails(container)
    const row = document.querySelector('[data-testid="task-agent"]')!
    expect(row.textContent).toContain('芝士')
    expect(row.textContent).toContain('跟随频道')
    expect(row.querySelector('button')).toBeNull()
  })

  it('不是负责人：看得到这件事交给了哪位队友，不能换', async () => {
    me = 'bob'
    const { container } = mount({ agent_handle: 'cheese-01' })
    await openDetails(container)
    const row = document.querySelector('[data-testid="task-agent"]')!
    expect(row.textContent).toContain('小苔')
    expect(row.querySelector('button')).toBeNull()
  })

  it('关了的任务换不了队友', async () => {
    const { container } = mount({ status: 'closed' })
    await openDetails(container)
    expect(document.querySelector('[data-testid="task-agent-edit"]')).toBeNull()
  })

  it('名册上没有别的队友可挑，就不给这颗「改」', async () => {
    const { container } = mount({}, PEOPLE, [])
    await openDetails(container)
    expect(document.querySelector('[data-testid="task-agent-edit"]')).toBeNull()
  })

  it('后端拒了这次更换，菜单留着让他再挑一次', async () => {
    const { container, setAgent } = mount({ agent_handle: 'cheese-01' })
    setAgent.mockResolvedValue(false)
    await openAgentMenu(container)
    await fireEvent.click(agentRows()[2])
    await waitFor(() => expect(setAgent).toHaveBeenCalledWith('cheese-02'))
    expect(agentMenuOpen()).toBe(true)
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

  it('关了的任务，负责人能重新打开；协作者不能', async () => {
    stubViewport()
    const owner = mount({ status: 'closed' })
    await fireEvent.click(owner.container.querySelector('[data-testid="task-more"]')!)
    await waitFor(() => expect(document.querySelector('[data-testid="task-reopen"]')).not.toBeNull())
    await fireEvent.click(document.querySelector('[data-testid="task-reopen"]')!)
    expect(owner.reopen).toHaveBeenCalledOnce()
    owner.unmount()

    me = 'bob'
    const helper = mount({ status: 'closed', contributor_handles: ['bob'] })
    await fireEvent.click(helper.container.querySelector('[data-testid="task-more"]')!)
    await waitFor(() => expect(document.querySelector('[data-testid="task-rename"]')).not.toBeNull())
    expect(document.querySelector('[data-testid="task-reopen"]')).toBeNull()
  })

  it('「⋯」打开的是一个菜单，每一项是菜单项', async () => {
    const { container } = mount()
    stubViewport()
    await fireEvent.click(container.querySelector('[data-testid="task-more"]')!)
    const menu = await waitFor(() => {
      const el = document.querySelector('[role="menu"]')
      expect(el).not.toBeNull()
      return el!
    })
    const items = Array.from(menu.querySelectorAll('[role="menuitem"]')).map((el) => el.textContent?.trim())
    expect(items).toEqual(expect.arrayContaining(['重命名', '关闭任务']))
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

  it('负责人挑过头像，页头画的就是他挑的那张图', async () => {
    const { container } = mount({}, PICKED)
    await nextTick()
    const img = container.querySelector('.task-header__owner img')
    expect(img, '挑过头像就该画那张图').not.toBeNull()
    expect(img?.getAttribute('src')).toContain('/avatars/7')
  })

  it('没挑过头像的人画按 handle 派生的首字母，不去取全站默认那张脸', async () => {
    // `avatar_id` 为 null 就是「从来没挑过」。拿它去取 `/avatars/default` 会让所有没
    // 挑过头像的人共用同一张脸 —— 那比没有头像更认不出是谁。
    const { container } = mount({ owner_handle: 'bob' }, PICKED)
    await nextTick()
    const owner = container.querySelector('.task-header__owner')!
    expect(owner.querySelector('img'), '不该去取任何一张图').toBeNull()
    expect(owner.querySelector('.user-avatar-char')?.textContent?.trim()).toBe('B')
  })

  it('协作者的头像各按自己挑过的画', async () => {
    const { container } = mount({ owner_handle: 'bob', contributor_handles: ['carol'] }, PICKED)
    await nextTick()
    const helper = container.querySelector('.task-header__helper')!
    expect(helper.querySelector('img')?.getAttribute('src')).toContain('/avatars/9')
  })
})
