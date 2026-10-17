// @vitest-environment jsdom
// 总览页上点了「让 AI 队友起草」，跳到项目目标那篇文档：它一打开、能问 AI 队友了，就
// 让 AI 队友起草，补充的话一起带上；别的文档不接这一件。
import type { DocAgentListener, DocAgentRequest } from '../../lib/docAgent'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { requestDraft, takeDraft } from '../../lib/overviewDraft'
import { DOC_TOPIC, docPanelProps, docSession } from '../../views/demo/catalogFixtures'

import PanelDocView from './PanelDocView.vue'

import { setLocale } from '@/i18n'

vi.mock('@tiptap/extension-drag-handle-vue-3', () => ({ DragHandle: { render: () => null } }))

beforeEach(() => {
  setLocale('zh-CN')
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
      unobserve() {}
    }
  )
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    scale: 1,
    addEventListener() {},
    removeEventListener() {},
  })
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  takeDraft('goals')
})

type Ask = (request: DocAgentRequest, listener: DocAgentListener) => Promise<void>

function open(documentId: string, askAgent: Ask) {
  return render(PanelDocView, {
    props: docPanelProps({
      topic: DOC_TOPIC,
      session: docSession('还没有内容。'),
      document: { id: documentId, projectId: 'p', title: '' },
      agentHandle: 'cheese',
      askAgent,
    }),
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

it('项目目标那篇一打开，AI 队友就带着补充的话起草', async () => {
  const askAgent = vi.fn<Ask>(async () => {})
  requestDraft('goals', '期末交一个能演示的小程序')
  open('goals', askAgent)
  await vi.waitFor(() => expect(askAgent).toHaveBeenCalledTimes(1))
  expect(askAgent.mock.calls[0][0]).toMatchObject({ preset: 'draft', text: '期末交一个能演示的小程序' })
})

it('别的文档不接起草', async () => {
  const askAgent = vi.fn<Ask>(async () => {})
  requestDraft('goals', '补充')
  open('weekly', askAgent)
  await new Promise((resolve) => setTimeout(resolve, 50))
  expect(askAgent).not.toHaveBeenCalled()
})
