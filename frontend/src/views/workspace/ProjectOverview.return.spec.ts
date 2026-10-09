// 回到看过的项目总览：先照着上一次读到的那份画，背后重取——不先清空成一片骨架。
// 用的是真的总览容器和画面；接口换成替身，第二次进来时它们一直不回来。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const answers = vi.hoisted(() => ({ hang: false }))
const later = <T>(value: T): Promise<T> => (answers.hang ? new Promise(() => {}) : Promise.resolve(value))

vi.mock('vue-router', async (importOriginal) => ({
  ...(await importOriginal<typeof import('vue-router')>()),
  useRouter: () => ({ push: vi.fn() }),
}))
vi.mock('@/me', () => ({ myHandle: () => 'alice' }))
// 文档正文的排版是另一件事：这里只看正文在不在。
vi.mock('@/components/common/MarkdownView.vue', () => ({
  default: { name: 'MarkdownView', props: ['source'], template: '<div>{{ source }}</div>' },
}))
vi.mock('@/stores/workspace', () => ({
  useWorkspaceStore: () => ({
    projects: [{ id: 'p1', name: '项目一' }],
    members: [],
    accessDenied: null,
    noteAccess: () => false,
  }),
}))
vi.mock('@/api/projectDocuments', () => ({
  getProjectOverview: vi.fn(() => later({ id: 'doc-1' })),
  getDocumentText: vi.fn(() => later('这是项目一要做的事')),
}))
vi.mock('@/api/projectProgress', () => ({
  listProjectProgress: vi.fn(() => later({ data: [], total: 0 })),
}))
vi.mock('@/api/tasks', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/tasks')>()),
  listProjectTasks: vi.fn(() => later({ data: [], total: 0 })),
}))
vi.mock('@/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api')>()),
  listProjectArtifacts: vi.fn(async () => ({ data: [], total: 0 })),
  getProjectSite: vi.fn(async () => {
    throw new Error('no site')
  }),
}))

import ProjectOverview from './ProjectOverview.vue'

import { setLocale } from '@/i18n'

function open() {
  return render(ProjectOverview, {
    props: { projectId: 'p1' },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

async function settle() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

beforeEach(() => {
  setLocale('zh-CN')
  answers.hang = false
  vi.stubGlobal('visualViewport', {
    width: 1280,
    height: 800,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
  Object.defineProperty(globalThis, 'devicePixelRatio', { configurable: true, value: 1 })
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it('回到看过的总览，项目总览那份文档马上就在，不等重取', async () => {
  const first = open()
  await settle()
  expect(first.container.textContent).toContain('这是项目一要做的事')
  first.unmount()

  answers.hang = true
  const again = open()
  await settle()

  expect(again.container.textContent).toContain('这是项目一要做的事')
})
