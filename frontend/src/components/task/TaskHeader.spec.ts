/** 任务页头和任务信息：只有负责人能换做它的电脑、能换做它的队友；别人看得到它在哪台电脑上、交给了哪位队友。做这件事的人都能改名。 */
import type { Component } from 'vue'
import type { RoomTask, Topic } from '@/cx_types'
import type { TopicEnvironment } from '@/types/compute'

import { nextTick } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
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
    created_at: '2026-10-05T00:00:00Z',
    updated_at: '2026-10-05T00:00:00Z',
    presentation: { column: 'needs_you', phrase: 'awaiting_review' },
    ...over,
  } as RoomTask
}

const LAPTOP = { kind: 'self_hosted', device_id: 'd1' } as const
const MACHINE: TopicEnvironment = {
  default: LAPTOP,
  default_from: 'room',
  devices: [{ device_id: 'd1', name: '王宁的笔记本', online: true, owned: true }],
  sessions: [
    {
      session_id: 's1',
      agent_handle: 'cheese-01',
      seat_handle: 'cheese-01',
      environment: LAPTOP,
      status: 'ready',
      online: true,
    },
  ],
  specs: [{ spec: 'standard', label: '标准' }],
}

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

/** 对话那一种摆法里页头那一组：文件和终端开着，改动改了两个文件。 */
const CHAT_BLOCKS = [
  { key: 'overview', label: '概览', icon: 'mdi-file-document-outline', on: false },
  { key: 'changes', label: '改动', hint: '改动（2 个文件）', icon: 'mdi-source-branch', on: false, count: 2 },
  { key: 'code', label: '文件', icon: 'mdi-folder-outline', on: true },
  { key: 'terminals', label: '终端', icon: 'mdi-console', on: true },
  { key: 'site', label: '现场', icon: 'mdi-hammer-wrench', on: false, more: true },
]

/** `worksTasks`：此刻看的人的角色有没有「新建任务和审阅」这一项（项目成员默认有，导师没有）。 */
function mount(over: Partial<RoomTask> = {}, people = PEOPLE, agents = AGENTS, worksTasks = true) {
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
      actionError: null,
      close: async () => true,
      reopen,
      handOver: async () => true,
      rename,
      setCollaborators,
      setAgent,
      loadMachine,
      setMachine: vi.fn(async () => ({})),
      setMachineDefault: vi.fn(async () => ({})),
      worksTasks,
    },
    global: { plugins: [vuetify] },
  })
  return { ...view, loadMachine, setCollaborators, setAgent, rename, reopen }
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

async function onPhone<T>(run: () => Promise<T>): Promise<T> {
  const prevWidth = window.innerWidth
  window.innerWidth = 390
  window.dispatchEvent(new Event('resize'))
  try {
    return await run()
  } finally {
    window.innerWidth = prevWidth
    window.dispatchEvent(new Event('resize'))
  }
}

/** 任务信息「状态」那一行里的几格。 */
async function stateFacts(container: Element): Promise<string[]> {
  await openDetails(container)
  return [...document.querySelectorAll('[data-testid="task-state-part"]')].map((el) => el.textContent ?? '')
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
  // 「未连接」只在真的断了的时候说：页面没告诉页头连没连上，就当连着。
  it('没说断了就不写「未连接」', () => {
    const view = mount()
    expect(view.queryByText('未连接')).toBeNull()
  })

  // 页头那一格和频道里的卡、全部任务、总览说同一件事：标签只说「待审阅」，后面那一行
  // 说在等谁——是看的人就说「等你」。
  // 桌面页头只摆状态那一格；在等谁、原话这些各自成一格，写在任务信息的「状态」里。
  it('标签只说待审阅；等的是看的人，任务信息里写等你，等的是别人就写出是谁', async () => {
    me = 'alice'
    const mine = mount({ waiting_on: 'alice', needs_my_action: true })
    expect(mine.getByTestId('task-phrase').textContent).toBe('待审阅')
    expect(mine.queryByTestId('task-progress-note')).toBeNull()
    expect(await stateFacts(mine.container)).toContain('待你处理')
    mine.unmount()
    document.body.innerHTML = ''

    const theirs = mount({ owner_handle: 'bob', waiting_on: 'bob' })
    expect(theirs.getByTestId('task-phrase').textContent).toBe('待审阅')
    const facts = await stateFacts(theirs.container)
    expect(facts).toContain('待 bob 处理')
    expect(facts).not.toContain('待你处理')
  })

  it('关注人不被写成实际答题人', async () => {
    const view = mount({
      presentation: { column: 'needs_you', phrase: 'awaiting_answer' },
      waiting_on: 'bob',
      awaits_me: true,
      needs_my_action: false,
    })
    const facts = await stateFacts(view.container)
    expect(facts).not.toContain('待你处理')
    expect(facts).toContain('待 bob 处理')
  })

  it('多人问题的实际被问者看到等你', async () => {
    const view = mount({
      presentation: { column: 'needs_you', phrase: 'awaiting_answer' },
      waiting_on: null,
      awaits_me: true,
      needs_my_action: true,
    })
    expect(await stateFacts(view.container)).toContain('待你处理')
  })

  it('待审阅的几样事各占一格，不连成一句', async () => {
    const view = mount({
      waiting_on: 'alice',
      needs_my_action: true,
      card: { id: 'c1', status: 'pending', version: 2, reviewer: 'alice' },
    } as Partial<RoomTask>)
    const facts = await stateFacts(view.container)
    expect(facts).toEqual(['待你处理', '第 2 版'])
  })

  // 队友报的那一句是看板这一格的出处：照说，会话走了也不改它，只在旁边说一声。
  it('队友说在等结果：页头写进行中，它的原话在任务信息里，会话走了也照旧，旁边写已离开', async () => {
    const said = {
      presentation: { column: 'building', phrase: 'waiting' },
      progress_state: 'waiting',
      progress_note: '等 CI 跑完',
      progress_set_by: 'cheese-01',
      progress_set_at: '2026-10-05T00:00:00Z',
    } as Partial<RoomTask>
    const here = mount(said)
    expect(here.getByTestId('task-phrase').textContent).toBe('进行中')
    expect(await stateFacts(here.container)).toContain('等 CI 跑完')
    expect(here.queryByTestId('task-agent-left')).toBeNull()
    here.unmount()
    document.body.innerHTML = ''

    const gone = mount({ ...said, agent_left_at: '2026-10-05T01:00:00Z' })
    expect(gone.getByTestId('task-phrase').textContent).toBe('进行中')
    expect(gone.getByTestId('task-agent-left').textContent).toContain('已离开')
    expect(await stateFacts(gone.container)).toContain('等 CI 跑完')
  })

  // 名字跟的是报那句话的队友：任务中途换过人时，两处不是同一个，照任务当前的队友
  // 写就把别人的「离开」栽到了还在干活的那位头上。
  it('已离开说的是报那一句的队友，不是任务现在的队友', () => {
    const swapped = mount({
      agent_handle: 'cheese-02',
      presentation: { column: 'building', phrase: 'running' },
      progress_state: 'working',
      progress_note: '在改页头',
      progress_set_by: 'cheese-01',
      progress_set_at: '2026-10-05T00:00:00Z',
      agent_left_at: '2026-10-05T01:00:00Z',
    })
    expect(swapped.getByTestId('task-agent-left').textContent).toBe('小苔已离开')
  })

  it('任务关了，就不再摆它报过的状态', () => {
    const { queryByTestId } = mount({
      status: 'closed',
      presentation: { column: 'done', phrase: 'closed' },
      progress_state: 'waiting',
      progress_note: '等 CI 跑完',
      agent_left_at: '2026-10-05T01:00:00Z',
    })
    expect(queryByTestId('task-progress-note')).toBeNull()
    expect(queryByTestId('task-agent-left')).toBeNull()
  })

  it('开着的任务，不做这件事的人：没有更多操作', () => {
    me = 'bob'
    const { container } = mount()
    expect(container.querySelector('[data-testid="task-more"]')).toBeNull()
  })

  it('负责人在任务信息里看得到每条会话的环境，也能更换', async () => {
    const { container, loadMachine } = mount()
    await openDetails(container)
    expect(loadMachine).toHaveBeenCalled()
    const row = document.querySelector('[data-testid="session-env-s1"]')!
    expect(row.textContent).toContain('小苔：王宁的笔记本')
    expect(row.querySelector('button')).not.toBeNull()
  })

  it('不是负责人：看得到任务在哪个环境里做，不能更换', async () => {
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

  it('关了的任务，负责人能在任务信息里重新打开', async () => {
    stubViewport()
    const owner = mount({ status: 'closed' })
    await fireEvent.click(owner.container.querySelector('[data-testid="task-details"]')!)
    await waitFor(() => expect(document.querySelector('[data-testid="task-reopen"]')).not.toBeNull())
    await fireEvent.click(document.querySelector('[data-testid="task-reopen"]')!)
    expect(owner.reopen).toHaveBeenCalledOnce()
  })

  it('关了的任务，不做这件事的项目成员也能重新打开', async () => {
    stubViewport()
    me = 'bob'
    const member = mount({ status: 'closed' })
    await fireEvent.click(member.container.querySelector('[data-testid="task-details"]')!)
    await waitFor(() => expect(document.querySelector('[data-testid="task-reopen"]')).not.toBeNull())
    await fireEvent.click(document.querySelector('[data-testid="task-reopen"]')!)
    expect(member.reopen).toHaveBeenCalledOnce()
  })

  it('角色没有「新建任务和审阅」的人（导师）重新打开不了关了的任务', async () => {
    me = 'teacher'
    const { container } = mount({ status: 'closed' }, PEOPLE, AGENTS, false)
    await openDetails(container)
    expect(document.querySelector('[data-testid="task-reopen"]')).toBeNull()
  })

  it('桌面上页头没有管任务的 ⋯：那一份在侧栏右键和命令面板里', () => {
    const { container } = mount({ contributor_handles: ['bob'] })
    expect(container.querySelector('[data-testid="task-more"]')).toBeNull()
  })

  it('手机上顶栏的「⋯」是那一份：复制链接、重命名，然后关闭任务', async () => {
    stubViewport()
    await onPhone(async () => {
      const { container } = mount()
      const more = await waitFor(() => {
        const el = container.querySelector('[data-testid="task-more"]')
        expect(el).not.toBeNull()
        return el!
      })
      await fireEvent.click(more)
      const items = await waitFor(() => {
        const found = Array.from(document.querySelectorAll('[role="menuitem"]')).map((el) => el.textContent?.trim())
        expect(found.length).toBeGreaterThan(0)
        return found
      })
      expect(items).toEqual(['重命名', '关闭任务'])
    })
  })

  it('协作者在任务信息里能改名、能关闭，不能转交', async () => {
    me = 'bob'
    const { container, rename } = mount({ contributor_handles: ['bob'] })
    await openDetails(container)
    expect(document.querySelector('[data-testid="task-close"]')).not.toBeNull()
    expect(document.querySelector('[data-testid="task-hand-over"]')).toBeNull()
    await fireEvent.click(screen.getByRole('button', { name: '编辑' }))
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

  it('手机顶栏里协作者的头像各按自己挑过的画', async () => {
    await onPhone(async () => {
      const { container } = mount({ owner_handle: 'bob', contributor_handles: ['carol'] }, PICKED)
      await nextTick()
      const helper = container.querySelector('.task-header__helper')!
      expect(helper.querySelector('img')?.getAttribute('src')).toContain('/avatars/9')
    })
  })

  it('负责人能在任务信息里转交', async () => {
    const { container } = mount()
    await openDetails(container)
    await fireEvent.click(document.querySelector('[data-testid="task-hand-over"]')!)
    await waitFor(() => expect(document.body.textContent).toContain('转交给谁？'))
  })

  it('手机顶栏里状态区保留完整进度说明与「已离开」，状态文字更新时横向滚动回到开头', async () => {
    const prevWidth = window.innerWidth
    window.innerWidth = 390
    window.dispatchEvent(new Event('resize'))
    try {
      const base = {
        presentation: { column: 'building', phrase: 'running' },
        progress_state: 'working',
        progress_note: '已按决定保持现状并更新实况文档，等待负责人确认是否关闭任务或继续讨论',
        progress_set_by: 'cheese-01',
        progress_set_at: '2026-10-05T00:00:00Z',
        agent_left_at: '2026-10-05T01:00:00Z',
      } as Partial<RoomTask>
      const { container, getByTestId, rerender } = mount(base)
      await nextTick()
      const header = container.querySelector('.task-header')!
      expect(header.classList.contains('task-header--bar')).toBe(true)
      expect(getByTestId('task-progress-note').textContent).toContain('已按决定保持现状')
      expect(getByTestId('task-agent-left').textContent).toBe('小苔已离开')
      expect(getByTestId('task-details')).not.toBeNull()
      expect(getByTestId('task-more')).not.toBeNull()

      const meta = container.querySelector<HTMLElement>('.task-header__meta')!
      meta.scrollLeft = 120
      expect(meta.scrollLeft).toBe(120)

      await rerender({ task: task({ ...base, progress_note: '正在跑回归测试' }) })
      await nextTick()
      expect(meta.scrollLeft).toBe(0)
    } finally {
      window.innerWidth = prevWidth
      window.dispatchEvent(new Event('resize'))
    }
  })

  it('桌面上页面几块并排时，页头有「对话 / 编辑器」开关，标着此刻是哪一种，点另一种就换', async () => {
    const { getByRole, rerender, emitted } = mount()
    await rerender({ form: 'chat' })
    const group = getByRole('radiogroup', { name: '摆法' })
    expect(getByRole('radio', { name: '对话' }).getAttribute('aria-checked')).toBe('true')
    expect(getByRole('radio', { name: '编辑器' }).getAttribute('aria-checked')).toBe('false')
    expect(group).not.toBeNull()
    await fireEvent.click(getByRole('radio', { name: '编辑器' }))
    expect(emitted('form')).toEqual([['editor']])
  })

  it('没得选（页面没交给外壳并排摆开）时不摆那个开关', () => {
    const { queryByRole } = mount()
    expect(queryByRole('radiogroup', { name: '摆法' })).toBeNull()
  })

  it('手机上不摆那个开关', async () => {
    const prevWidth = window.innerWidth
    window.innerWidth = 390
    window.dispatchEvent(new Event('resize'))
    try {
      const { queryByRole, rerender } = mount()
      await rerender({ form: 'chat' })
      expect(queryByRole('radiogroup', { name: '摆法' })).toBeNull()
    } finally {
      window.innerWidth = prevWidth
      window.dispatchEvent(new Event('resize'))
    }
  })

  it('对话那一种摆法里一样东西一颗，开着的是按下的样子；点开着的关掉，点关着的打开', async () => {
    const { getByRole, queryByRole, rerender, emitted } = mount()
    await rerender({ form: 'chat', blocks: CHAT_BLOCKS })
    expect(getByRole('button', { name: '概览' }).getAttribute('aria-pressed')).toBe('false')
    expect(getByRole('button', { name: '文件' }).getAttribute('aria-pressed')).toBe('true')
    expect(getByRole('button', { name: '终端' }).getAttribute('aria-pressed')).toBe('true')
    expect(getByRole('button', { name: '改动（2 个文件）' }).getAttribute('aria-pressed')).toBe('false')
    // 现场、预览不直接摆出来，在 ⋯ 里。
    expect(queryByRole('button', { name: '现场' })).toBeNull()
    await fireEvent.click(getByRole('button', { name: '文件' }))
    await fireEvent.click(getByRole('button', { name: '概览' }))
    await fireEvent.click(getByRole('button', { name: '更多' }))
    await fireEvent.click(await waitFor(() => getByRole('menuitemcheckbox', { name: '现场' })))
    expect(emitted('toggle-block')).toEqual([['code'], ['overview'], ['site']])
  })

  it('编辑器里「概览」那颗换成左栏、终端、对话三颗，各自说着那一块在不在，点了就藏起或摆回', async () => {
    const { getByRole, queryByRole, rerender, emitted } = mount()
    await rerender({
      form: 'chat',
      blocks: CHAT_BLOCKS,
      editorParts: { left: true, terminal: false, conversation: true },
    })
    expect(queryByRole('button', { name: '左栏' })).toBeNull()
    expect(getByRole('button', { name: '概览' })).not.toBeNull()

    await rerender({ form: 'editor', blocks: [], editorParts: { left: true, terminal: false, conversation: true } })
    expect(queryByRole('button', { name: '概览' })).toBeNull()
    expect(getByRole('button', { name: '左栏' }).getAttribute('aria-pressed')).toBe('true')
    expect(getByRole('button', { name: '终端' }).getAttribute('aria-pressed')).toBe('false')
    expect(getByRole('button', { name: '对话' }).getAttribute('aria-pressed')).toBe('true')
    await fireEvent.click(getByRole('button', { name: '终端' }))
    await fireEvent.click(getByRole('button', { name: '对话' }))
    expect(emitted('toggle-part')).toEqual([['terminal'], ['conversation']])
  })

  it('编辑器里页头只留任务名和状态：有未提交的修改、负责人收在任务名旁边那一颗里', async () => {
    const { getByRole, queryByRole, queryByText, rerender } = mount({
      presentation: { column: 'needs_you', phrase: 'awaiting_review' },
      card: { id: 'c1', status: 'pending', version: 1, reviewer: 'alice' },
    } as Partial<RoomTask>)
    await rerender({ form: 'chat', unhanded: true })
    expect(queryByText('有未提交的修改')).not.toBeNull()
    expect(getByRole('button', { name: 'alice 负责' })).not.toBeNull()

    await rerender({ form: 'editor', unhanded: true, editorParts: { left: true, terminal: true, conversation: true } })
    expect(queryByText('有未提交的修改')).toBeNull()
    expect(queryByRole('button', { name: 'alice 负责' })).toBeNull()
    await fireEvent.click(getByRole('button', { name: '任务信息' }))
    await waitFor(() => expect(queryByText('有未提交的修改')).not.toBeNull())
    expect(queryByText('负责人')).not.toBeNull()
  })

  it('编辑器里 ⋯ 里有在中间打开概览、预览的两项', async () => {
    const { getByRole, rerender, emitted } = mount()
    await rerender({
      form: 'editor',
      editorParts: { left: true, terminal: true, conversation: true },
      blocks: [
        { key: 'overview', label: '概览', icon: 'mdi-file-document-outline', on: false, more: true },
        { key: 'preview', label: '预览', icon: 'mdi-eye-outline', on: false, more: true },
      ],
    })
    await fireEvent.click(getByRole('button', { name: '更多操作' }))
    await fireEvent.click(await waitFor(() => getByRole('menuitem', { name: '预览' })))
    expect(emitted('toggle-block')).toEqual([['preview']])
  })
})
