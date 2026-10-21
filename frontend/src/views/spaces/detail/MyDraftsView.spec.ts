/** 「我的草稿」：列出新题目和对已发布题目的修改，点名字接着写，删之前问一句。 */
import type { TaskDraftSummary } from '@/types'

import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import MyDraftsView from './MyDraftsView.vue'

import i18n, { setLocale } from '@/i18n'

beforeEach(() => {
  setLocale('zh-CN')
  vi.stubGlobal('visualViewport', new EventTarget())
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const summary = (over: Partial<TaskDraftSummary>): TaskDraftSummary => ({
  id: 1,
  spaceId: 7,
  taskId: null,
  taskName: null,
  name: '',
  source: '',
  conversationId: null,
  version: 1,
  updatedAt: new Date().toISOString(),
  missing: [],
  ...over,
})

function show(drafts: TaskDraftSummary[], more: Record<string, unknown> = {}) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: { template: '<div />' } }],
  })
  return render(MyDraftsView, {
    props: { drafts, loading: false, failed: false, removing: false, publishTo: '/', ...more },
    global: { plugins: [router, createVuetify({ components, directives }), i18n] },
  })
}

describe('我的草稿', () => {
  it('写出名称和来源，修改已发布题目的那份标着「修改」', () => {
    const view = show([
      summary({ id: 41, name: '课题 A', source: '实验室科研课题-2026秋.pdf' }),
      summary({ id: 42, taskId: 88, taskName: 'Lab 3 缓存模拟器' }),
    ])

    view.getByText('课题 A')
    view.getByText('从 实验室科研课题-2026秋.pdf 导入')
    view.getByText('Lab 3 缓存模拟器')
    view.getByText('修改')
  })

  it('点名字打开那一份', async () => {
    const draft = summary({ id: 41, name: '课题 A' })
    const view = show([draft])
    await fireEvent.click(view.getByText('课题 A'))

    expect(view.emitted('open')).toEqual([[draft]])
  })

  it('删除先问一句，确认了才删', async () => {
    const view = show([summary({ id: 41, name: '课题 A' })])
    await fireEvent.click(view.getByTestId('my-draft-remove-41'))
    expect(view.emitted('remove')).toBeUndefined()

    expect(await screen.findByText('删除「课题 A」？')).toBeTruthy()
    const buttons = Array.from(screen.getByRole('alertdialog').querySelectorAll('button'))
    await fireEvent.click(buttons.find((b) => b.textContent?.trim() === '删除')!)

    expect(view.emitted('remove')).toEqual([[41]])
  })

  it('一份都没有时说暂无草稿', () => {
    const view = show([])
    view.getByText('暂无草稿')
  })
})
