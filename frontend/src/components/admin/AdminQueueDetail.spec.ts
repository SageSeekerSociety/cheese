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

  // 读屏念的是框上的名字：标签在框外，它得被认成这个框的名字，不能是空的或「打开」。
  it('指派给和优先级两个下拉框都叫它们自己的名字', async () => {
    const { findByRole } = mountDetail({ item: ROW, loading: false, error: null, triageOpen: true })

    expect(await findByRole('combobox', { name: '优先级' })).toBeTruthy()
    expect(await findByRole('combobox', { name: /指派给/ })).toBeTruthy()
  })

  // 右栏顶上那一行是「谁提的 · 时间 · 支持 · 评论 · 指派给谁」。作者是个人，所以那一行
  // 也得画脸 —— 但只有作者画 chip：后面那一整句仍是一条文字流（`text-overflow` 才有东西
  // 可截），指派那一张脸就在下面的事实格里。
  it('摘要那一行的作者画的是脸 + 昵称，后面那整句还是一条文字流', () => {
    const { container } = mountDetail({
      item: { ...ROW, author_name: 'Max', author_avatar_id: 7 },
      loading: false,
      error: null,
      triageOpen: false,
    })

    const who = container.querySelector('.qdet__main-who')
    expect(who, '摘要那一行没画出来').not.toBeNull()
    const chip = who!.querySelector('.identity-view')
    expect(chip, '摘要那行的作者没画身份').not.toBeNull()
    expect(chip!.querySelector('.identity-view__name')?.textContent).toBe('Max')
    expect(chip!.querySelector('img')?.getAttribute('src')).toContain('/avatars/7')
    expect(who!.querySelector('.qdet__who-run')?.textContent).toContain('指派')
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
