// 项目总览：项目总览那份文档照原样显示，没写过就给一个「写一份」；谁在做什么按负责人
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

import { setLocale, t } from '@/i18n'

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
      names: { alice: 'Alice', bob: 'Bob' },
      avatars: {},
      me: 'alice',
      artifactApi,
      ...extra,
    },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

it('项目总览那份文档照原样显示', async () => {
  const view = mount({ overviewText: '## 现在到哪了\n\n报名和审核已经能用。' })
  const doc = view.getByTestId('overview-document')
  await vi.waitFor(() => expect(doc.textContent).toContain('报名和审核已经能用。'))
  expect(doc.textContent).toContain('现在到哪了')
})

it('没写过项目总览：点「写一份」去写', async () => {
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

it('任务和进展还没读到：不写「0」，也不写「暂无」', () => {
  const view = mount({ overviewText: null, progress: null, tasks: null })
  expect(view.getByTestId('overview-all-tasks').textContent).not.toMatch(/\d/)
  expect(view.queryByText(t('work.overview.nobody'))).toBeNull()
  expect(view.queryByText(t('work.overview.noProgress'))).toBeNull()
})
