// 项目总览：项目目标照原样显示，没写过就给一个去写的入口；谁在做什么按负责人
// 分、我在最前，停住的和做完的不在里面；最近进展里点一件就去那件任务。任务和进展还
// 没读到时不写「0」、不写「暂无」。
import type { RoomTask } from '@/cx_types'
import type { ProgressItem } from '@/types/projectProgress'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import ProjectOverviewView from './ProjectOverviewView.vue'

import i18n, { setLocale, t } from '@/i18n'
import { renderNoticeMessage } from '@/lib/noticeText'

Object.defineProperty(globalThis, 'devicePixelRatio', { configurable: true, value: 1 })

let n = 0
function task(over: Partial<RoomTask>): RoomTask {
  n += 1
  return {
    id: `t${n}`,
    project_id: 'p',
    room_id: 'front',
    title: `任务 ${n}`,
    status: 'open',
    owner_handle: 'bob',
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
    presentation: { column: 'building', phrase: 'started' },
    ...over,
  }
}

const artifactApi = {
  list: vi.fn(async () => ({ data: [], total: 0 })),
  rename: vi.fn(),
  merge: vi.fn(),
  remove: vi.fn(),
  site: {
    read: vi.fn(async () => {
      throw new Error('no site')
    }),
    publish: vi.fn(),
  },
}

beforeEach(() => {
  vi.stubGlobal('visualViewport', {
    width: 1280,
    height: 800,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
  setLocale('zh-CN')
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function mount(extra: Record<string, unknown> = {}) {
  return render(ProjectOverviewView, {
    props: {
      projectId: 'p',
      projectName: '课程项目',
      overviewText: '',
      progress: [],
      progressFailed: false,
      tasks: [],
      underway: 0,
      names: { alice: 'Alice', bob: 'Bob' },
      avatars: {},
      me: 'alice',
      artifactApi,
      ...extra,
    },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

it('项目目标照原样显示', async () => {
  const view = mount({ overviewText: '## 现在到哪了\n\n报名和审核已经能用。' })
  const doc = view.getByTestId('overview-document')
  await vi.waitFor(() => expect(doc.textContent).toContain('报名和审核已经能用。'))
  expect(doc.textContent).toContain('现在到哪了')
})

it('没写过项目目标：点去写的入口', async () => {
  const view = mount({ overviewText: '' })
  await fireEvent.click(view.getByRole('button', { name: t('work.overview.write') }))
  expect(view.emitted('edit-overview')).toHaveLength(1)
})

it('谁在做什么：我在最前；停住的、做完的不在里面', () => {
  const view = mount({
    tasks: [
      task({ title: 'Bob 在做的' }),
      task({ title: '我的', owner_handle: 'alice' }),
      task({ title: '停住的', stalled: true }),
      task({ title: '做完的', status: 'closed', presentation: { column: 'done', phrase: 'completed' } }),
    ],
  })
  const people = view.getByTestId('overview-people')
  const names = Array.from(people.querySelectorAll('.ov-person__name')).map((el) => el.textContent)
  expect(names).toEqual(['Alice', 'Bob'])
  expect(people.textContent).not.toContain('停住的')
  expect(people.textContent).not.toContain('做完的')
})

it('最近进展里点一件，去那件任务', async () => {
  const item: ProgressItem = {
    kind: 'started',
    at: new Date().toISOString(),
    taskId: 't9',
    taskTitle: '首页加载慢',
    taskTitleSource: 'human',
    roomId: 'front',
    by: 'bob',
    artifactId: null,
    artifactName: '',
    version: null,
  }
  const view = mount({ progress: [item] })
  await fireEvent.click(view.getByText('首页加载慢'))
  expect(view.emitted('open-task')).toEqual([[{ taskId: 't9', roomId: 'front' }]])
})

function revised(version: number, changes: ProgressItem['changes']): ProgressItem {
  return {
    kind: 'revised',
    at: new Date().toISOString(),
    taskId: null,
    taskTitle: '',
    taskTitleSource: null,
    roomId: null,
    by: null,
    artifactId: null,
    artifactName: '',
    version,
    changes,
  }
}

it('题目更新了：写到了第几版、改了什么，点开去看历史版本里的那一版', async () => {
  const item = revised(3, { rubric: true, files: { replaced: ['数据.csv'] } })
  const view = mount({ progress: [item], taskLinked: true })
  const line = view.getByText('第 3 版：改了评分要点、替换 数据.csv')
  await fireEvent.click(line)
  expect(view.emitted('open-task-version')).toEqual([[3]])
})

it('看不到这道题的人：题目更新那一行是一行字，不是一颗点不动的按钮', async () => {
  const view = mount({ progress: [revised(3, { rubric: true })], taskLinked: false })
  const line = view.getByText('第 3 版：改了评分要点')
  expect(line.closest('button')).toBeNull()
  await fireEvent.click(line)
  expect(view.emitted('open-task-version')).toBeUndefined()
})

it('只换了起始代码的那一版：写明换了起始代码', () => {
  const view = mount({ progress: [revised(2, { starter: 'replaced' })] })
  expect(view.getByText('第 2 版：替换起始代码')).toBeTruthy()
})

it('题目的阶段改了：这一版写明改了阶段', () => {
  const item: ProgressItem = {
    kind: 'revised',
    at: new Date().toISOString(),
    taskId: null,
    taskTitle: '',
    taskTitleSource: null,
    roomId: null,
    by: null,
    artifactId: null,
    artifactName: '',
    version: 2,
    changes: { stages: true },
  }
  const view = mount({ progress: [item] })
  expect(view.getByText('第 2 版：改了阶段')).toBeTruthy()
})

it('没绑平台账号的 GitHub 合并人：原样显示登录名，不去名册里撞同名的人', () => {
  const item: ProgressItem = {
    kind: 'accepted',
    at: new Date().toISOString(),
    taskId: 't9',
    taskTitle: 'GitHub 上直接合的那次',
    taskTitleSource: 'human',
    roomId: 'front',
    by: 'octocat',
    byGithub: true,
    artifactId: null,
    artifactName: '',
    version: null,
  }
  const view = mount({
    progress: [item],
    names: { alice: 'Alice', bob: 'Bob', octocat: '另一个人的名字' },
  })
  const progress = view.getByTestId('overview-progress')
  expect(progress.textContent).toContain('octocat')
  expect(progress.textContent).not.toContain('另一个人的名字')
})

it('任务和进展还没读到：不写「0」，也不写「暂无」', () => {
  const view = mount({ overviewText: null, progress: null, tasks: null })
  expect(view.getByTestId('overview-all-tasks').textContent).not.toMatch(/\d/)
  expect(view.queryByText(t('work.overview.nobody'))).toBeNull()
  expect(view.queryByText(t('work.overview.noProgress'))).toBeNull()
})

it('「进行中的任务」那一个数用的是传进来的那一份，不是拿列表自己数', () => {
  // 清单第 14 条：这个数（后端 `underway_counts`）含不含停在那儿的任务，由说明那一句讲
  // 清楚；数字本身不再从列里凑，所以给几件任务也不影响它。
  const view = mount({
    underway: 7,
    tasks: [task({ title: 'A' }), task({ title: 'B' }), task({ title: 'C' })],
  })
  const button = view.getByTestId('overview-all-tasks')
  expect(button.textContent).toContain('7')
  expect(button.getAttribute('title')).toBe(t('work.sidebar.underwayHint'))
})

it('领题开的项目：写着这份报名自己截到哪一刻，按看的人的钟点到分钟', () => {
  // 本地时间造出来的时刻：不管测试机在哪个时区，这一刻在本地都是 10 月 24 日 16:05。
  const at = new Date(2030, 9, 24, 16, 5).getTime()
  const view = mount({ overviewText: '## 赛题要求', challengeDeadline: { at, mine: true } })
  expect(view.getByTestId('overview-deadline').textContent).toBe('提交截止：10月24日 16:05')

  cleanup()
  const closing = mount({ overviewText: '## 赛题要求', challengeDeadline: { at, mine: false } })
  expect(closing.getByTestId('overview-deadline').textContent).toBe('题目截止：10月24日 16:05')
})

it('领的题分了阶段：那一行截止换成阶段时间轴，写着这个项目每个阶段走到哪一步', async () => {
  const at = new Date(2030, 9, 24, 16, 5).getTime()
  const view = mount({
    overviewText: '## 赛题要求',
    challengeDeadline: { at, mine: true },
    challengeStages: {
      stages: [
        { name: '需求分析', description: '', dueAt: at },
        { name: '实现', description: '', dueAt: null },
      ],
      statuses: ['PASSED', 'NOT_SUBMITTED'],
    },
  })
  expect(view.queryByTestId('overview-deadline')).toBeNull()
  const timeline = await view.findByTestId('overview-stages')
  expect(timeline.textContent).toContain('需求分析')
  expect(timeline.textContent).toContain('截止 10月24日 16:05')
  expect(timeline.textContent).toContain('已通过')
  expect(timeline.textContent).toContain('未提交')
})

it('不是领题开的项目：不写截止', () => {
  const view = mount({ overviewText: '## 现在到哪了' })
  expect(view.queryByTestId('overview-deadline')).toBeNull()
})

it('仓库从题目的起始代码开始的：在放、放好了、没放成各说一句；没有起始代码的不写', () => {
  const pending = mount({ overviewText: '## 赛题要求', starter: { revision: 3, state: 'pending', reason: '' } })
  expect(pending.getByTestId('overview-starter').textContent?.trim()).toBe('代码仓库正在放入题目第 3 版的起始代码')

  cleanup()
  const done = mount({ overviewText: '## 赛题要求', starter: { revision: 3, state: 'done', reason: '' } })
  expect(done.getByTestId('overview-starter').textContent?.trim()).toBe('代码仓库的起始代码来自题目第 3 版')

  cleanup()
  const failed = mount({
    overviewText: '## 赛题要求',
    starter: { revision: 3, state: 'failed', reason: '压缩包里没有文件' },
  })
  expect(failed.getByTestId('overview-starter').textContent?.trim()).toBe(
    '题目第 3 版的起始代码没能放进代码仓库：压缩包里没有文件'
  )

  cleanup()
  const plain = mount({ overviewText: '## 赛题要求', starter: null })
  expect(plain.queryByTestId('overview-starter')).toBeNull()
})

it('起始代码没放成：接着说下一步怎么办，「题目」文件夹点得开；在放、放好了的不说', async () => {
  const failed = mount({
    overviewText: '## 赛题要求',
    starter: { revision: 3, state: 'failed', reason: '压缩包里没有文件' },
  })
  expect(failed.getByTestId('overview-starter-next').textContent).toContain('起始代码.zip')
  await fireEvent.click(failed.getByRole('button', { name: '「题目」文件夹' }))
  expect(failed.emitted('open-task-folder')).toEqual([[]])

  cleanup()
  const pending = mount({ overviewText: '## 赛题要求', starter: { revision: 3, state: 'pending', reason: '' } })
  expect(pending.queryByTestId('overview-starter-next')).toBeNull()

  cleanup()
  const done = mount({ overviewText: '## 赛题要求', starter: { revision: 3, state: 'done', reason: '' } })
  expect(done.queryByTestId('overview-starter-next')).toBeNull()
})

it('main 先被占了而没放成的：题目第几版的起始代码只说一次', () => {
  const view = mount({
    overviewText: '## 赛题要求',
    starter: {
      revision: 3,
      state: 'failed',
      reason: renderNoticeMessage({ key: 'starterMainTaken' }, ''),
    },
  })
  const line = view.getByTestId('overview-starter').textContent ?? ''
  expect(line.split('题目第 3 版').length - 1).toBe(1)
  expect(line).toContain('main 分支')
})

it('没写过项目目标：让 AI 队友起草，补充的话一起交出去', async () => {
  const view = mount({ overviewText: '', agentName: '芝士' })
  await fireEvent.click(view.getByRole('button', { name: t('work.overview.draft', { agent: '芝士' }) }))
  await fireEvent.update(view.getByRole('textbox'), '期末要交一个能演示的小程序')
  await fireEvent.click(view.getByRole('button', { name: t('work.overview.draftStart') }))
  expect(view.emitted('draft-overview')).toEqual([[{ note: '期末要交一个能演示的小程序', files: [] }]])
})

it('写过项目目标：没有起草的入口', () => {
  const view = mount({ overviewText: '做一个预约小程序。', agentName: '芝士' })
  expect(view.queryByRole('button', { name: t('work.overview.draft', { agent: '芝士' }) })).toBeNull()
})

it('现状：写成过就在最上面，还没有可写的就不占地方', async () => {
  const written = mount({
    agentName: '芝士',
    situation: { content: '报名流程已可用，当前重点是内测', written_at: new Date().toISOString(), writing: false },
  })
  await vi.waitFor(() =>
    expect(written.getByTestId('overview-situation').textContent).toContain('报名流程已可用，当前重点是内测')
  )
  cleanup()
  const empty = mount({ agentName: '芝士', situation: { content: '', written_at: null, writing: false } })
  expect(empty.queryByTestId('overview-situation')).toBeNull()
})
