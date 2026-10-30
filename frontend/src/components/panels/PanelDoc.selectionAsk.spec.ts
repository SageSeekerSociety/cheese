// @vitest-environment jsdom
/**
 * 一段选区上只能有一颗「问芝士」。
 *
 * 文档自己那条浮条（`doc/DocBubble.vue`）在选中一段字时给一个 AI 队友按钮，条件是这一格
 * 的 `agentHandle` 有值；页面外壳那颗全局的「问芝士」（`components/assistant/AgentSelectionAsk.vue`）
 * 看的是选区外面那层 `data-agent-select`。两处判据必须是同一个，否则两颗按钮会叠在同一段
 * 选区上——这正是负责人报的「概览的实况文档部分还是没有删干净」。
 *
 * 这里钉住的是**开关本身**：它跟着「这一格有没有文档芝士」走，不是跟着 `document.projectId`
 * 走。任务的实况文档由 `taskId` 打开、根本没有 `document` 这个 prop，而它有文档芝士。
 *
 * `PanelDocView` 换成一颗透传的壳：真身带着编辑器、协同那一大坨，而这一组测试要看的只是
 * 「`PanelDoc` 把哪个值传了下去」。
 */
import { defineComponent, h } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import PanelDoc from './PanelDoc.vue'

import i18n, { setLocale } from '@/i18n'
import { docBundle, docPeopleBundle, docThreadsBundle } from '@/views/demo/catalogPanelsFixtures'

const Passthrough = defineComponent({
  name: 'PanelDocView',
  setup:
    (_props, { attrs }) =>
    () =>
      h('div', { ...attrs, 'data-testid': 'doc-view' }),
})

/** 渲染这一格，读它给正文那一层标的 `data-agent-select`。 */
function agentSelect(props: Record<string, unknown>): string | null {
  const view = render(defineComponent({ setup: () => () => h(PanelDoc as never, props) }), {
    global: {
      plugins: [createVuetify({ components, directives }), i18n],
      stubs: { PanelDocView: Passthrough },
    },
  })
  const found = view.container.querySelector<HTMLElement>('[data-testid="doc-view"]')
  if (!found) throw new Error('PanelDocView 没渲染出来')
  return found.getAttribute('data-agent-select')
}

const base = () => ({
  docPanel: docBundle(),
  docThreads: docThreadsBundle(),
  docPeople: docPeopleBundle(),
  activityTick: 0,
  topic: null,
})

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

describe('一段选区上的问芝士', () => {
  it('任务的实况文档：taskId 打开、没有 document prop、有文档芝士 → 不出全局那颗', () => {
    expect(agentSelect({ ...base(), taskId: 'task-1', agentHandle: 'cheese-task' })).toBe('off')
  })

  it('项目资料库的一份文档：document + 文档芝士 → 也不出', () => {
    const document = { id: 'd1', projectId: 'p1', title: '目标', created_by: null, drafted_by: null }
    expect(agentSelect({ ...base(), document, agentHandle: 'cheese-task' })).toBe('off')
  })

  it('团队文档：没有文档芝士 → 照常出全局那颗', () => {
    const document = { id: 'd2', projectId: 'team-1', title: '须知', created_by: null, drafted_by: null }
    expect(agentSelect({ ...base(), document, agentHandle: null })).toBe('on')
  })
})
