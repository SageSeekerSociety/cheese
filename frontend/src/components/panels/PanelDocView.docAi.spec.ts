// @vitest-environment jsdom
import { defineComponent, h, nextTick, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { useDocAi } from '../../composables/useDocAi'
import { setLocale, t } from '../../i18n'
import { DOC_TOPIC, docPanelProps } from '../../views/demo/catalogFixtures'

import DocAiPanel from './doc/DocAiPanel.vue'
import PanelDocView from './PanelDocView.vue'

const api = vi.hoisted(() => ({ source: vi.fn(), create: vi.fn() }))
vi.mock('@/api/docAi', () => ({
  getDocAiSource: api.source,
  createDocAiRequest: api.create,
  listDocAiRequests: async () => ({ requests: [] }),
  getDocAiRequest: vi.fn(),
  getDocAiProposal: vi.fn(),
  acceptDocAiProposal: vi.fn(),
  cancelDocAiRequest: vi.fn(),
}))

beforeEach(() => {
  setLocale('zh-CN')
  localStorage.clear()
  sessionStorage.clear()
  localStorage.setItem('user', JSON.stringify({ id: 'human', username: 'human' }))
  api.source.mockImplementation(async (topic: string) => ({
    document_id: `doc-${topic}`,
    source: 'text',
    base_version: 4,
    offset_unit: 'utf8-bytes',
    nodes: [],
  }))
  api.create.mockResolvedValue({ request_id: 'request', state: 'pending' })
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const width = this.classList.contains('doc-reading') ? 1000 : 300
    return { x: 0, y: 0, left: 0, top: 0, width, right: width, height: 600, bottom: 600, toJSON: () => ({}) }
  })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.clearAllMocks()
})

it('asks the destination room after switching rooms and reopening AI through the shared tab', async () => {
  const topic = ref({ ...DOC_TOPIC, id: 'ai-room-a' })
  let ai!: ReturnType<typeof useDocAi>
  render(
    defineComponent({
      setup() {
        ai = useDocAi({
          topic: () => topic.value.id,
          raw: () => 'text',
          prefix: () => '',
          version: () => 4,
          blocked: () => false,
          reload: async () => {},
        })
        return () =>
          h(
            PanelDocView,
            {
              ...docPanelProps(),
              topic: topic.value,
              commentAuthor: 'human',
              aiOpened: ai.opened.value,
              onOpenAi: ai.prepare,
              onCloseAi: () => (ai.opened.value = false),
            },
            {
              ai: () =>
                h(DocAiPanel, {
                  docked: true,
                  cards: ai.cards.value,
                  question: ai.question.value,
                  busy: ai.busy.value,
                  error: ai.error.value,
                  selectionStatus: ai.selectionStatus.value,
                  hasSelection: !!ai.selection.value,
                  blocked: false,
                  unknown: !!ai.unknown.value,
                  version: 4,
                  'onUpdate:question': (value: string) => (ai.question.value = value),
                  onSubmit: ai.submit,
                  onRecover: ai.recover,
                }),
            }
          )
      },
    }),
    { global: { plugins: [createVuetify({ components, directives })] } }
  )

  await fireEvent.click(screen.getByRole('button', { name: /^文档 AI$/ }))
  await waitFor(() => expect(api.source).toHaveBeenCalledTimes(1))
  await fireEvent.update(screen.getByRole('textbox', { name: '问题或修改要求' }), 'room A question')
  topic.value = { ...topic.value, id: 'ai-room-b' }
  await nextTick()
  expect(screen.queryByRole('textbox', { name: '问题或修改要求' })).toBeNull()
  await fireEvent.click(screen.getByRole('button', { name: /^评论$/ }))
  await fireEvent.click(screen.getByRole('tab', { name: /^文档 AI$/ }))
  await fireEvent.update(screen.getByRole('textbox', { name: '问题或修改要求' }), 'room B question')
  await fireEvent.click(screen.getByRole('button', { name: t('work.room.docAi.ask') }))
  await waitFor(() =>
    expect(api.create).toHaveBeenCalledWith(
      'ai-room-b',
      expect.objectContaining({ question: 'room B question', document_id: 'doc-ai-room-b' })
    )
  )
})
