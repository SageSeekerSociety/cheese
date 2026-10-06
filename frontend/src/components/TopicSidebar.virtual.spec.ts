// 话题列表交给虚拟列表的那一条路：行多到撑不住以后，屏幕上少挂几行，但三件事不能丢
// ——置顶行和组头照旧在（它们不在那一组里），选中的那一行照旧留在 DOM 里（光标在它上
// 面），以及「选中了就把它带进视口」照旧做得到（行不在 DOM 里时只能按序号滚）。
//
// happy-dom 不排版，所以这里不量「屏幕上挂着几行」：量出来的是我们喂给 virtua 的假
// 高度。这一份问的是 virtua 收到了什么、以及选中之后有没有人来按序号滚。
import type { Component } from 'vue'
import type { Topic } from '@/cx_types'

import { defineComponent, h } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import { VLayout } from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import TopicSidebar from './TopicSidebar.vue'

import { setLocale } from '@/i18n'

const virtua = vi.hoisted(() => ({
  seen: [] as { which: string; props: Record<string, unknown> }[],
  scrollToIndex: vi.fn(),
}))

// 挂一条 120 行的话题列表（Vuetify 的行 + 路由 + pinia）本身就要好几秒，整套一起跑、
// 机器又满的时候会顶到默认的 5 秒上限。这份里量的是「virtua 收到了什么」，不是快慢，
// 所以把上限放宽——挂完再等一会儿 virtua 露头，别让它在排队时被误判成失败。
vi.setConfig({ testTimeout: 30_000 })
const WAIT = { timeout: 10_000 }

vi.mock('virtua/vue', async () => {
  const { defineComponent: define, h: hyperscript } = await import('vue')
  const make = (which: 'VList' | 'Virtualizer') =>
    define({
      name: which,
      props: ['data', 'itemSize', 'bufferSize', 'keepMounted', 'itemProps', 'scrollRef', 'item'],
      setup(props, { attrs, slots, expose }) {
        virtua.seen.push({ which, props: props as unknown as Record<string, unknown> })
        expose({ scrollToIndex: virtua.scrollToIndex })
        // 签名照抄真实的那份：`itemProps` 收的是一个 `{ item, index }` 对象，
        // 每一行的外壳标签由 `item` 说（不给就是 div）。
        const itemProps = props.itemProps as
          | ((payload: { item: unknown; index: number }) => Record<string, unknown>)
          | undefined
        const itemTag = (props.item as string | undefined) || 'div'
        return () =>
          hyperscript(
            'div',
            { 'data-virtua': which, ...attrs },
            ((props.data as unknown[]) ?? []).map((item, index) =>
              hyperscript(itemTag, { ...(itemProps?.({ item, index }) ?? {}) }, slots.default?.({ item, index }))
            )
          )
      },
    })
  return { VList: make('VList'), Virtualizer: make('Virtualizer') }
})

// 行尾那股 ⋯ 菜单（桌面形态）是 Vuetify 的 v-menu：一展开就要摸 `window.visualViewport`
// （happy-dom 没有），当场炸，还会把整个文件的后续用例带崩。这里换一只只会「点一下就把
// 菜单打开」的替身——这一份要验的是「菜单开着的那一行不能被摘掉」，菜单自己长什么样不
// 归它管。
vi.mock('./common/AdaptiveMenu.vue', async () => {
  const { defineComponent: define, h: hyperscript } = await import('vue')
  return {
    default: define({
      name: 'AdaptiveMenuStub',
      props: { modelValue: Boolean },
      emits: ['update:modelValue'],
      setup(_props, { slots, emit }) {
        return () =>
          hyperscript('div', {}, slots.activator?.({ props: { onClick: () => emit('update:modelValue', true) } }))
      },
    }),
  }
})

beforeEach(() => {
  setLocale('zh-CN')
  virtua.seen.length = 0
  virtua.scrollToIndex.mockClear()
})

const Sidebar = TopicSidebar as unknown as Component

function topic(id: string, parentId: string | null, flags: Partial<Topic> = {}): Topic {
  return {
    id,
    project_id: 'p1',
    parent_id: parentId,
    title: id,
    kind: parentId === null ? 'root' : 'topic',
    status: 'active',
    created_by: 'u',
    created_at: '2026-08-10T00:00:00Z',
    updated_at: '2026-08-10T00:00:00Z',
    ...flags,
  } as Topic
}

/** 一行都不少地建 N 个我参与的话题：它们全在上组（`mine`），就是会被虚拟化的那一组。 */
function longRail(n: number): Topic[] {
  return [topic('root', null), ...Array.from({ length: n }, (_, i) => topic(`t${i}`, 'root', { joined: true }))]
}

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/:pathMatch(.*)*', name: 'catch-all', component: defineComponent({ setup: () => () => h('div') }) },
  ],
})

const Host = defineComponent({
  props: { inner: { type: Object, required: true } },
  setup(props) {
    return () => h(VLayout, null, { default: () => [h(Sidebar, props.inner as Record<string, unknown>)] })
  },
})

function inner(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    projects: [{ id: 'p1', name: 'P1', created_at: '2026-08-10T00:00:00Z' }],
    selectedProjectId: 'p1',
    topics: longRail(120),
    selectedTopicId: null,
    loadingTopics: false,
    ...overrides,
  }
}

function mount(overrides: Record<string, unknown> = {}) {
  const vuetify = createVuetify({ components, directives })
  return render(Host, {
    props: { inner: inner(overrides) },
    global: { plugins: [vuetify, router, createPinia()] },
  })
}

/** 交给 virtua 的那一份数据（`data` 是话题行，不是 `Topic` 本身）。 */
function virtualizedRows(): { topic: Topic }[] {
  return virtua.seen[0].props.data as { topic: Topic }[]
}

describe('行多到撑不住的时候', () => {
  it('这一组交给虚拟列表，整份行数原样递过去', async () => {
    mount()
    // 滚动容器是模板 ref，比第一帧晚一点落地：等它一下（同一轮里就换过来了）。
    await waitFor(() => expect(virtua.seen).toHaveLength(1), WAIT)
    expect(virtua.seen.map((s) => s.which)).toEqual(['Virtualizer'])
    expect(virtualizedRows()).toHaveLength(120)
    // 门槛和递进来的那几个数：36 上下是紧凑行的高度，估得太离谱第一屏会跳一次。
    expect(virtua.seen[0].props.itemSize).toBe(36)
    expect(virtua.seen[0].props.bufferSize).toBe(320)
  })

  it('置顶行和组头不在虚拟化的那一份里 —— 它们本来就不随话题列表滚', async () => {
    const { container } = mount()
    await waitFor(() => expect(virtua.seen).toHaveLength(1), WAIT)
    // 虚拟化的那一份只有话题行：全局（root）那一条是在外面画的。
    expect(virtualizedRows().every((row) => row.topic.id !== 'root')).toBe(true)
    expect(container.querySelectorAll('.pinned-row').length).toBeGreaterThan(0)
  })
})

describe('行数不到门槛的时候', () => {
  it('一行 virtua 都不碰 —— 阈值以内和以前一模一样', () => {
    const { container } = mount({ topics: longRail(3) })
    expect(virtua.seen).toHaveLength(0)
    // 行还是老老实实挂在 DOM 里（FLIP 换位和 Tab 走位都靠这个）。
    expect(container.querySelectorAll('[data-room-id]').length).toBeGreaterThanOrEqual(3)
  })
})

describe('选中的话题在窗口外', () => {
  it('按序号让那一组滚过去，而不是去找一个找不到的 DOM', async () => {
    // 那一行可能压根没被窗口挂上，`querySelector` 会扑空——这时候唯一够得着它的办法
    // 是告诉那一组「滚到第 N 行」。
    const { rerender } = mount()
    await rerender({ inner: inner({ selectedTopicId: 't119' }) })
    await waitFor(() => expect(virtua.scrollToIndex).toHaveBeenCalledWith(119, { align: 'nearest' }), WAIT)
  })

  it('选中的那一行无论滚到哪儿都留在 DOM 里', async () => {
    // 不在 DOM 里的行不会被 focus 到：光标正停在它上面时把它摘掉，焦点就丢了。
    const { rerender } = mount()
    await rerender({ inner: inner({ selectedTopicId: 't119' }) })
    await waitFor(() => expect(virtua.seen[0].props.keepMounted).toEqual([119]), WAIT)
  })
})

describe('锚点落在行上的那几种状态', () => {
  it('⋯ 菜单开着的那一行也留在 DOM 里 —— 那颗 ⋯ 就是菜单的 activator', async () => {
    // 菜单展开期间那颗 ⋯ 必须留在屏幕上：它是 activator，跟着窗口一起消失菜单就塌了。
    const { container } = mount()
    await waitFor(() => expect(virtua.seen).toHaveLength(1), WAIT)
    const btn = container.querySelector('[data-room-id="t5"] .row-actions__btn') as HTMLElement | null
    expect(btn).not.toBeNull()
    await fireEvent.click(btn as HTMLElement)
    await waitFor(() => expect(virtua.seen[0].props.keepMounted as readonly number[]).toContain(5), WAIT)
  })
})
