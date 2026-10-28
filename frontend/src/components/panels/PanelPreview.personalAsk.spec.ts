/**
 * 去处里的「问个人芝士（私聊）」：同一句话和同一处位置，交给个人芝士那一条对话，
 * 不进这间房。
 *
 * 守三件事：
 *   - 发出去的那一句带着位置（哪一份文件、哪一格、这一格当时的内容）——个人芝士不在
 *     这间房里，它只有这句话。
 *   - 同时不落进房间的对话：`locate` 不出现、提问出口也不被调。
 *   - 登录了才有这一项；没有时那一项不列出来，主按钮仍发进房间。
 */
import type { SubmitPreviewQuestion } from '../../lib/previewQuestion'

import { h } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import PanelPreviewView from './PanelPreviewView.vue'

import { setLocale } from '@/i18n'
import { previewBundles } from '@/test/panelBundles'

const agent = vi.hoisted(() => ({
  available: { value: false },
  open: vi.fn(),
  ask: vi.fn(),
}))

vi.mock('@/stores/agent', async () => {
  const { ref: makeRef } = await import('vue')
  agent.available = makeRef(false)
  return {
    agentAvailable: agent.available,
    openAgent: agent.open,
    askAgent: agent.ask,
  }
})

vi.mock('./preview/PreviewSheet.vue', () => ({
  default: {
    props: ['data', 'kind', 'marks', 'activeMark'],
    emits: ['cell', 'range', 'mark'],
    setup(_props: unknown, { emit }: { emit: (e: string, p?: unknown) => void }) {
      return () =>
        h('div', { 'data-testid': 'sheet' }, [
          h(
            'button',
            {
              onClick: () => emit('cell', { address: 'B7', value: '1200', sheet: 'Sheet1' }),
            },
            'cell'
          ),
        ])
    },
  },
}))
vi.mock('./preview/RevisionList.vue', () => ({ default: { template: '<div />' } }))

const docBytes = new ArrayBuffer(8)
const context = {
  topicId: 'room',
  path: 'budget.xlsx',
  source: 'committed' as const,
  taskId: 'task',
  version: 'v7',
}

function props(overrides: Record<string, unknown> = {}) {
  return {
    ...previewBundles(),
    topicId: 'room',
    projectId: 'project',
    frameName: 'frame',
    loading: false,
    refreshing: false,
    previewFile: {
      path: 'budget.xlsx',
      content: null,
      version: 'v7',
      bytes: 8,
      binary: true,
      too_large: false,
      source: context.source,
    },
    previewMime: '',
    previewNamed: true,
    previewUrl: null,
    previewAppNote: '',
    previewTunnelUp: false,
    previewNamedPath: 'budget.xlsx',
    previewError: null,
    previewReadError: null,
    documentSuffix: 'xlsx',
    documentType: {
      view: 'sheet' as const,
      label: 'spreadsheet',
      icon: 'mdi-file-excel-outline',
      sheet: 'workbook' as const,
    },
    documentName: 'budget.xlsx',
    isImageArtifact: false,
    downloadError: '',
    docBytes,
    docLoading: false,
    docError: '',
    docRendererMissing: false,
    docIdentity: context,
    docSnapshot: { bytes: docBytes, identity: context, sourceVersion: context.version },
    slideContext: context,
    ...overrides,
  }
}

beforeEach(() => {
  setLocale('zh-CN')
  agent.available.value = false
  agent.open.mockClear()
  agent.ask.mockClear()
})
afterEach(cleanup)

function mount(submitQuestion?: SubmitPreviewQuestion) {
  return render(PanelPreviewView, {
    props: { ...props(), submitQuestion },
    global: {
      plugins: [createVuetify({ components, directives })],
      stubs: {
        VBtn: { template: '<button><slot /></button>' },
        VIcon: true,
        VSpacer: true,
        VAlert: true,
        VDialog: true,
      },
    },
  })
}

it('选「问个人芝士」发出去：带着位置进私聊，房间的对话和提问出口都不动', async () => {
  agent.available.value = true
  const submit = vi.fn().mockReturnValue(true)
  const ui = mount(submit as unknown as SubmitPreviewQuestion)
  await fireEvent.click(ui.getByText('cell'))
  await fireEvent.update(ui.getByPlaceholderText('输入修改要求或批注内容…'), '这个数字按季度摊')
  await fireEvent.click(ui.getByTestId('locator-recipient'))
  await fireEvent.click(ui.getByTestId('locator-recipient-personal'))
  await fireEvent.click(ui.getByText('发送'))

  expect(agent.open).toHaveBeenCalled()
  expect(agent.ask).toHaveBeenCalledTimes(1)
  const message = agent.ask.mock.calls[0]?.[0] as string
  expect(message).toContain('这个数字按季度摊')
  expect(message).toContain('budget.xlsx')
  expect(message).toContain('B7')
  expect(message).toContain('1200')
  // 私聊的那一句不落进房间：结构化提问没被调，也没有退回拼一句话那条路。
  expect(submit).not.toHaveBeenCalled()
  expect(ui.emitted().locate).toBeUndefined()
  // 发完这一处就收起，和发进房间时一样。
  expect(ui.queryByPlaceholderText('输入修改要求或批注内容…')).toBeNull()
})

it('没登录时那一项不列出来，主按钮照旧发进房间', async () => {
  const submit = vi.fn().mockReturnValue(true)
  const ui = mount(submit as unknown as SubmitPreviewQuestion)
  await fireEvent.click(ui.getByText('cell'))
  await fireEvent.update(ui.getByPlaceholderText('输入修改要求或批注内容…'), '这个数字按季度摊')
  await fireEvent.click(ui.getByTestId('locator-recipient'))
  expect(ui.queryByTestId('locator-recipient-personal')).toBeNull()
  await fireEvent.click(ui.getByText('发送'))

  expect(agent.ask).not.toHaveBeenCalled()
  expect(submit).toHaveBeenCalledTimes(1)
})
