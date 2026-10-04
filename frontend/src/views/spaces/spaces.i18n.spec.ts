// 英文界面下，空间这几页不该再冒出中文：发题页（手写与 PDF 两条路）、PDF 解析后的草稿区，
// 以及数据看板里学习那一格的几张卡。挂的是真的 i18n，锁到 en。
//
// 发题页底下那张表单和附件卡片（`components/tasks/`）换成壳：它们的文案不归这一页管，
// 这里只量这一页自己画的字。用户与接口造出来的内容（题目名、空间名）特意用 ASCII，
// 免得把数据里的中文误判成没翻译的界面文字。
import type { Component } from 'vue'

import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import i18n, { setLocale } from '@/i18n'

const previewFromPdf = vi.fn()
const spaceDetail = vi.fn()
const listCategories = vi.fn()
const listDomainGroups = vi.fn()

vi.mock('@/network/api/tasks', () => ({
  TasksApi: {
    previewFromPdf: (...a: unknown[]) => previewFromPdf(...a),
    confirmFromPdf: vi.fn(),
    create: vi.fn(),
    list: vi.fn(),
  },
}))

vi.mock('@/network/api/spaces', () => ({
  SpacesApi: {
    detail: (...a: unknown[]) => spaceDetail(...a),
    listCategories: (...a: unknown[]) => listCategories(...a),
    listDomainGroups: (...a: unknown[]) => listDomainGroups(...a),
    listMaterials: vi.fn().mockResolvedValue({ data: { materials: [], canManage: false } }),
  },
}))

vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

vi.mock('@/components/tasks/TaskForm.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return { __esModule: true, default: defineComponent({ name: 'TaskFormStub', setup: () => () => h('form') }) }
})
vi.mock('@/components/tasks/TaskAttachmentPicker.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    __esModule: true,
    default: defineComponent({ name: 'TaskAttachmentPickerStub', setup: () => () => h('div') }),
  }
})

import LearningOutlineCard from './detail/analytics/components/LearningOutlineCard.vue'
import LearningStuckPointCard from './detail/analytics/components/LearningStuckPointCard.vue'
import PublishTask from './detail/PublishTask.vue'

import BarList from '@/components/spaces/BarList.vue'

const CJK = /[㐀-鿿]/
const SPACE_ID = 7

const stub = { render: () => null }

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/spaces/:spaceId/tasks/publish', name: 'SpacesDetailPublishTask', component: stub },
      { path: '/spaces/:spaceId/tasks', name: 'SpacesDetailTasksList', component: stub },
      { path: '/spaces/:spaceId/manage/audit', name: 'SpacesDetailAuditTasks', component: stub },
      { path: '/spaces/:spaceId/tasks/:taskId', name: 'TasksDetail', component: stub },
      { path: '/workspace/:projectId/:topicId', name: 'workspace-topic', component: stub },
    ],
  })
}

const previous = i18n.global.locale.value

beforeAll(() => {
  vi.stubGlobal('devicePixelRatio', 1)
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
  setLocale('en')
})

afterAll(() => {
  setLocale(previous)
  vi.unstubAllGlobals()
})

beforeEach(() => {
  spaceDetail.mockResolvedValue({ data: { space: { id: SPACE_ID, name: 'Systems', admins: [] } } })
  listCategories.mockResolvedValue({
    data: { categories: [{ id: 3, name: 'Basics', displayOrder: 1, archivedAt: null }] },
  })
  listDomainGroups.mockResolvedValue({ data: { groups: [] } })
  previewFromPdf.mockResolvedValue({
    data: {
      drafts: [
        {
          name: 'Find a segfault',
          intro: 'Use gdb.',
          description: 'A crash. ![fig](https://x.test/a.png)',
          space: SPACE_ID,
        },
      ],
      templateUsed: {},
      tokenUsed: 1200,
      attachments: { pdf: { id: 1, name: 'hw.pdf', size: 10, contentType: 'application/pdf' }, images: [] },
    },
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

async function mountPublish() {
  const router = makeRouter()
  await router.push(`/spaces/${SPACE_ID}/tasks/publish`)
  await router.isReady()
  const pinia = createPinia()
  setActivePinia(pinia)
  const view = render(PublishTask as Component, {
    global: { plugins: [createVuetify({ components, directives }), router, pinia, i18n] },
  })
  await waitFor(() => expect(view.container.querySelector('form')).not.toBeNull(), { timeout: 20_000 })
  return view
}

describe('spaces in English', () => {
  it('the publish page has no Chinese on either path, including parsed drafts', async () => {
    const view = await mountPublish()
    expect(view.container.textContent).not.toMatch(CJK)

    await fireEvent.click(view.getByRole('button', { name: 'Generate from PDF' }))
    await waitFor(() => expect(view.container.querySelector('[data-testid="pdf-file"]')).not.toBeNull())
    expect(view.container.textContent).not.toMatch(CJK)

    const input = view.getByLabelText('Upload a challenge PDF') as HTMLInputElement
    Object.defineProperty(input, 'files', {
      value: [new File([new Uint8Array(10)], 'hw.pdf', { type: 'application/pdf' })],
      configurable: true,
    })
    await fireEvent.change(input)
    await fireEvent.click(view.getByRole('button', { name: 'Parse into challenge drafts' }))
    await waitFor(() => expect(view.container.querySelector('[data-testid="pdf-meta"]')).not.toBeNull())

    expect(view.container.textContent).not.toMatch(CJK)
    const labels = Array.from(view.container.querySelectorAll('[aria-label],[title],[placeholder]')).map((el) =>
      ['aria-label', 'title', 'placeholder'].map((a) => el.getAttribute(a) ?? '').join(' ')
    )
    expect(labels.join(' ')).not.toMatch(CJK)
  }, 30_000)

  it('the learning cards and the bar list have no Chinese', () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const plugins = [createVuetify({ components, directives }), makeRouter(), pinia, i18n]
    const excerpt = {
      blockId: 'b1',
      quote: 'why does it loop',
      studentName: 'Ann',
      knowledgePoint: null,
      topicTitle: 'Loops',
      createdAt: null,
      projectId: 'p1',
      topicId: 't1',
    }

    const texts = [
      render(LearningStuckPointCard as Component, {
        props: {
          point: {
            knowledgePoint: null,
            studentCount: 2,
            projectCount: 1,
            questionCount: 3,
            latestAt: null,
            example: excerpt,
          },
        },
        global: { plugins },
      }),
      render(LearningOutlineCard as Component, {
        props: { outline: { sections: [], missing: ['b9'] } },
        global: { plugins },
      }),
      render(BarList as Component, { props: { rows: [] }, global: { plugins } }),
    ].map((view) => view.container.innerHTML)

    for (const html of texts) expect(html).not.toMatch(CJK)
  })

  it('the bar list counts people in English, one person and several people', () => {
    const plugins = [createVuetify({ components, directives }), i18n]
    const view = render(BarList as Component, {
      props: {
        rows: [
          { label: 'Team A', value: 3 },
          { label: 'Solo', value: 1 },
        ],
        format: (n: number) => i18n.global.t('tasks.insights.people', n),
      },
      global: { plugins },
    })
    expect(view.getByText('3 people')).toBeTruthy()
    expect(view.getByText('1 person')).toBeTruthy()
  })
})
