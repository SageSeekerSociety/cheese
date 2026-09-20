/**
 * 换话题时，上一个话题的提案不能画到当前话题的会话栏里。
 *
 * 这个组件在话题之间是**复用**的（同一个路由，只换参数），而 `load()` 是异步的、
 * 两次调用可以交叉：先发出的那次后到，就会把上一个话题的提案写在现在这个话题下面。
 * 而在这张卡上按「提交反馈」，递出去的是 `props.topicId` + 那个 block_id —— 服务端
 * 按话题校验 block，那条请求必回 404。也就是说人点下去只会得到一个报错，且卡片
 * 本身还是错的那一条。
 */
import type { FeedbackProposal } from '@/cx_types'

import { createRouter, createWebHashHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { describe, expect, it, vi } from 'vitest'

import AgentFeedbackCard from './AgentFeedbackCard.vue'

import { useFeedbackStore } from '@/stores/feedback'

function proposal(blockId: string, title: string): FeedbackProposal {
  return {
    block_id: blockId,
    author_handle: 'cheese',
    authored_at: '2026-09-19T00:00:00Z',
    payload: {
      kind: 'bug',
      title,
      summary: '',
      problem: '点什么都没反应',
      visibility: 'public',
      why: null,
      expectation: null,
      what_happened: null,
      repro: null,
      evidence: null,
      logs: null,
      session_id: null,
      environment: null,
      tags: [],
      user_said: '用户没有就这个问题说过话',
      fingerprint: blockId,
    },
  } as unknown as FeedbackProposal
}

/** 一个能由测试决定何时回话的请求。 */
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((r) => {
    resolve = r
  })
  return { promise, resolve }
}

/** 组件和测试必须共用这一个 pinia：spy 装在测试拿到的 store 上，组件得用同一个。 */
function setup() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const vuetify = createVuetify({ components, directives })
  const router = createRouter({
    history: createWebHashHistory(),
    routes: [{ path: '/:pathMatch(.*)*', component: { template: '<div />' } }],
  })
  // 卡片末尾挂着提交抽屉（一个 `v-navigation-drawer`），它要 `v-app` 的 layout。
  const Wrapper = {
    components: { AgentFeedbackCard },
    props: { topicId: { type: String, required: true } },
    template: '<v-app><AgentFeedbackCard :topic-id="topicId" /></v-app>',
  }
  return { pinia, vuetify, router, Wrapper }
}

describe('Agent 反馈卡换话题', () => {
  it('上一个话题迟到的提案不会画在当前话题下面', async () => {
    const { pinia, vuetify, router, Wrapper } = setup()
    const store = useFeedbackStore()
    const slow = deferred<FeedbackProposal[]>()
    const fast = deferred<FeedbackProposal[]>()
    vi.spyOn(store, 'loadProposals').mockImplementation((topicId: string) =>
      topicId === 't-old' ? slow.promise : fast.promise
    )

    const { baseElement, rerender } = render(Wrapper, {
      props: { topicId: 't-old' },
      global: { plugins: [vuetify, router, pinia] },
    })

    // 人还停在旧话题上、请求在飞，就切到了新话题（同一个组件、只换了参数）。
    // `rerender` 换的就是同一个实例的 props —— 正是路由换参数的等价物。
    await rerender({ topicId: 't-new' })

    // 新话题先回来。
    fast.resolve([proposal('b-new', '新话题的提案')])
    await waitFor(() => {
      expect(baseElement.textContent).toContain('新话题的提案')
    })

    // 旧话题后回来 —— 它已经不该再写了。
    slow.resolve([proposal('b-old', '旧话题的提案')])
    await new Promise((r) => setTimeout(r, 50))

    expect(baseElement.textContent).not.toContain('旧话题的提案')
  })
})
