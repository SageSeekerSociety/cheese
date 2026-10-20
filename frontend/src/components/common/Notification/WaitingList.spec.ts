// 等你处理清单里，点到任务负责人的那一条要说清楚它停在哪一格。AI 队友报了「卡住」，
// 负责人看到的应当是卡住了，而不是「已退回」——那件事并没有被退回过。
import type { WaitingWork } from '@/cx_types'

import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it } from 'vitest'

import WaitingList from './WaitingList.vue'

import i18n, { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))
afterEach(() => cleanup())

function ownerItem(phrase: WaitingWork['phrase']): WaitingWork {
  return {
    kind: 'work',
    projectId: 'p1',
    projectName: '数据看板',
    topicId: 't1',
    topicTitle: '周报',
    taskId: 'k1',
    taskTitle: '导出周报',
    phrase,
    reason: 'owner',
    at: new Date().toISOString(),
  }
}

async function mount(item: WaitingWork) {
  const Blank = defineComponent({ render: () => h('div') })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: Blank },
      { path: '/p/:projectId/t/:topicId/k/:taskId', name: 'workspace-task', component: Blank },
    ],
  })
  await router.push('/')
  await router.isReady()
  return render(WaitingList, {
    props: {
      items: [item],
      loading: false,
      failed: false,
      errorReason: '',
      actionError: '',
      acting: null,
      clearing: null,
      replying: null,
    },
    global: { plugins: [createVuetify({ components, directives }), router, i18n] },
  })
}

it('a task the AI teammate reported stuck reads as stuck, not as returned', async () => {
  const view = await mount(ownerItem('blocked'))
  expect(view.getByText('卡住了')).toBeTruthy()
  expect(view.queryByText('已退回')).toBeNull()
})

it('a task whose delivery was sent back still reads as returned', async () => {
  const view = await mount(ownerItem('bounced'))
  expect(view.getByText('已退回')).toBeTruthy()
})
