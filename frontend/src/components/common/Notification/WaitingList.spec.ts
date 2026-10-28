// 等你处理清单里，点到任务负责人的那一条要说清楚它停在哪一格。AI 队友报了「卡住」，
// 负责人看到的应当是「卡住」，而不是「退回」——那件事并没有被退回过。
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
  expect(view.getByText('卡住')).toBeTruthy()
  expect(view.queryByText('退回')).toBeNull()
})

it('a task whose delivery was sent back still reads as returned', async () => {
  const view = await mount(ownerItem('bounced'))
  expect(view.getByText('退回')).toBeTruthy()
})

// 卡住了的那一行，第二行是它要你做的事。AI 队友没留话时不能空着：负责人得知道该打开看看。
it('a stuck task with no note from its teammate says to open it and see what it needs', async () => {
  const view = await mount({ ...ownerItem('blocked'), detail: '' })
  expect(view.getByText('打开看看芝士需要什么')).toBeTruthy()
})

it('a stuck task whose teammate said what it needs shows that, not the generic line', async () => {
  const view = await mount({ ...ownerItem('blocked'), detail: '没有预发环境的测试账号' })
  expect(view.getByText('没有预发环境的测试账号')).toBeTruthy()
  expect(view.queryByText('打开看看芝士需要什么')).toBeNull()
})

// 等你审阅的那一行，改动主题后面说这是第几版：只看主题分不出是第一次交，还是退回后又交的一版。
it('a card waiting on your review says which version it is', async () => {
  const view = await mount({
    ...ownerItem('awaiting_review'),
    reason: 'reviewer',
    detail: '登录表单加「记住我」',
    cardVersion: 2,
    cardSince: new Date().toISOString(),
  })
  expect(view.getByText('登录表单加「记住我」')).toBeTruthy()
  expect(view.container.textContent).toContain('第 2 版')
})

// 项目给队友改过名，卡住那一句就叫它改过的名字，不叫默认的「芝士」。
it('a stuck task names the teammate by the name its project gave it', async () => {
  const view = await mount({ ...ownerItem('blocked'), detail: '', agentName: '小研', agentNameSource: 'human' })
  expect(view.getByText('打开看看小研需要什么')).toBeTruthy()
  expect(view.queryByText('打开看看芝士需要什么')).toBeNull()
})
