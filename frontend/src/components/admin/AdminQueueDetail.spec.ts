/**
 * 详情（`AdminQueueDetail`，§4.4，三个断点共用这一个组件）。
 *
 * 队列页那组用例只走到「抽屉挂上了、里面还没有 item」那一格：那些用例在默认视口下
 * 根本不展开详情内容。所以这个文件补的是**详情自己的三条分支** —— 拿到一条反馈画
 * 什么、还在拉的时候画什么、拉不到画什么。
 *
 * 断言只对着用户看得见的东西（文字、`aria-checked`、抛给调用方的事件），也**不替
 * store 或 api 造替身**：这一层只吃 props，给它 mock 一层才是不测 —— 「props 进来
 * 之后画错了」这个失败模式恰好会被替身盖住。
 */
import type { Component } from 'vue'
import type { FeedbackDetail } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { beforeAll, describe, expect, it } from 'vitest'

import AdminQueueDetail from './AdminQueueDetail.vue'

import i18n, { setLocale } from '@/i18n'

/** 一条形状齐全的反馈：详情里每一个 `v-if` 都尽量走到，免得测的是半条数据。 */
const ROW = {
  id: 'fb-3',
  display_id: 'FB-1040',
  title: '导出报表偶发 502',
  author_handle: 'maxiaoyu',
  author_is_agent: false,
  visibility: 'private',
  kind: 'bug',
  priority: 'normal',
  status: 'received',
  created_at: '2026-09-19T10:00:00Z',
  supports: 2,
  comments: 0,
  assignee_handle: null,
  security: false,
  problem: '导出的时候偶尔 502，重试一次又好了。',
  why: null,
  expectation: null,
  what_happened: null,
  repro: null,
  evidence: null,
  logs: null,
  session_id: null,
  environment: null,
  topic_id: null,
  project_id: null,
  timeline: [],
  thread: [],
  thread_next_cursor: null,
  notes: [],
} as unknown as FeedbackDetail

function mountDetail(props: {
  item: FeedbackDetail | null
  loading: boolean
  error: string | null
  triageOpen: boolean
}) {
  const vuetify = createVuetify({ components, directives })
  return render(AdminQueueDetail as unknown as Component, {
    props,
    global: { plugins: [vuetify, createPinia(), i18n] },
  })
}

beforeAll(() => {
  // 这一层的中文都在词表里，而 happy-dom 的 `navigator.language` 是 `en-US`。
  setLocale('zh-CN')
})

describe('管理端反馈详情', () => {
  it('拿到一条反馈：标题、编号、状态梯子都画出来，点下一格报给调用方', async () => {
    const { findByText, getByRole, emitted } = mountDetail({
      item: ROW,
      loading: false,
      error: null,
      triageOpen: true,
    })

    expect(await findByText('导出报表偶发 502')).toBeTruthy()
    expect(await findByText('FB-1040')).toBeTruthy()
    // 梯子上当前那一格是现状（标出来、不可点），别的格子是目的地。
    expect(getByRole('radio', { name: '已收录' }).getAttribute('aria-checked')).toBe('true')
    expect(getByRole('radio', { name: '处理中' }).getAttribute('aria-checked')).toBe('false')

    // 状态这一步**不落 store**：它要进调用方那条撤销条，所以只报上去。
    await fireEvent.click(getByRole('radio', { name: '处理中' }))
    expect(emitted().triage?.[0]).toEqual(['in_progress'])
  })

  it('还在拉的时候画骨架，不画「加载失败」', () => {
    const { container, queryByText } = mountDetail({
      item: null,
      loading: true,
      error: null,
      triageOpen: false,
    })

    expect(container.querySelectorAll('.qdet__skel').length).toBe(4)
    expect(queryByText('详情加载失败')).toBeNull()
  })

  it('拉不到时画「加载失败」和服务端那句原话', async () => {
    const { findByText } = mountDetail({ item: null, loading: false, error: '后端炸了', triageOpen: false })

    expect(await findByText('详情加载失败')).toBeTruthy()
    expect(await findByText('后端炸了')).toBeTruthy()
  })

  it('这条不在你能看的范围里时说的是「不存在」', async () => {
    const { findByText, queryByText } = mountDetail({
      item: null,
      loading: false,
      error: null,
      triageOpen: false,
    })

    expect(await findByText('这条反馈不存在')).toBeTruthy()
    expect(queryByText('详情加载失败')).toBeNull()
  })
})
