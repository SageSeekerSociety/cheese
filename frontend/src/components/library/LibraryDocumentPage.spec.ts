/**
 * 资料库文档页：「查看改动」的 review 要在文档真正到场之后交给面板外壳。
 *
 * 页面只吃 props：`document` 为 null 时连外壳都还没挂（异步取元数据的那一小段），
 * 这时 review 送不出去也送错了地方。这一份钉的是「元数据一到、外壳一挂，review 就
 * 跟着到」这条接线，以及换一份文档 / 换一次 review 时它跟着重送。engine（真正把
 * 改动标出来的那一半）在它自己的测试里，这里只看外壳收到了什么。
 */
import type { PanelDocument } from '../../composables/usePanelDoc'
import type { DocReviewRequest } from '../../lib/docReview'

import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { setLocale } from '@/i18n'

// 外壳那一刻收到了什么：哪一份文档、哪一次 review。engine 本身不在这里，外壳只
// 暴露 reviewEdits（真外壳的那一份接口在 PanelDocHost 自己的测试里）。
const host = vi.hoisted(() => ({
  reviewEdits: [] as { doc: string | null; request: DocReviewRequest }[],
}))

vi.mock('@/components/work/PanelDocHost.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    default: defineComponent({
      name: 'PanelDocHostStub',
      props: { document: { type: Object, default: null } },
      setup(props, { expose }) {
        expose({
          reviewEdits: (request: DocReviewRequest) =>
            host.reviewEdits.push({ doc: (props.document as PanelDocument | null)?.id ?? null, request }),
        })
        return () => h('div', { 'data-open-document': (props.document as PanelDocument | null)?.id ?? '' })
      },
    }),
  }
})

import LibraryDocumentPage from './LibraryDocumentPage.vue'

const review = (edits: { old: string; new: string }[]): DocReviewRequest => ({ requester: '林夏', edits })

const doc = (id: string): PanelDocument => ({ id, projectId: 'p1', title: `标题 ${id}` })

function mount() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ name: 'project-library', path: '/projects/:projectId/library', component: { template: '<div />' } }],
  })
  return render(LibraryDocumentPage, {
    props: {
      projectId: 'p1',
      document: null as PanelDocument | null,
      review: undefined as DocReviewRequest | undefined,
      error: '',
      agentName: '芝士',
      agentHandle: null,
      members: [],
      topicList: [],
    },
    global: { plugins: [createVuetify({ components, directives }), router] },
  })
}

beforeEach(() => {
  setLocale('zh-CN')
  host.reviewEdits = []
})

describe('资料库文档页的「查看改动」', () => {
  it('文档还在读的时候不送，元数据一到就送给外壳', async () => {
    const view = mount()
    // 元数据还没回来：外壳还没挂，没有地方接 review。
    expect(view.container.querySelector('[data-open-document]')).toBeNull()
    expect(host.reviewEdits).toHaveLength(0)

    const request = review([{ old: '合同', new: '协议' }])
    await view.rerender({ document: doc('d1'), review: request })

    await waitFor(() => expect(view.container.querySelector('[data-open-document="d1"]')).not.toBeNull())
    await waitFor(() => expect(host.reviewEdits).toHaveLength(1))
    expect(host.reviewEdits[0]).toMatchObject({ doc: 'd1', request })
  })

  it('换一份文档重挂外壳，review 也重新送过去', async () => {
    const request = review([{ old: '合同', new: '协议' }])
    const view = mount()
    await view.rerender({ document: doc('d1'), review: request })
    await waitFor(() => expect(host.reviewEdits).toHaveLength(1))

    await view.rerender({ document: doc('d2'), review: request })
    await waitFor(() => expect(view.container.querySelector('[data-open-document="d2"]')).not.toBeNull())
    await waitFor(() => expect(host.reviewEdits.some((call) => call.doc === 'd2')).toBe(true))
    expect(host.reviewEdits.at(-1)).toMatchObject({ doc: 'd2', request })
  })

  it('同一份文档上 review 变了，也重送', async () => {
    const first = review([{ old: '合同', new: '协议' }])
    const view = mount()
    await view.rerender({ document: doc('d1'), review: first })
    await waitFor(() => expect(host.reviewEdits).toHaveLength(1))

    const second = review([{ old: '初稿', new: '定稿' }])
    await view.rerender({ document: doc('d1'), review: second })
    await waitFor(() => expect(host.reviewEdits.at(-1)?.request).toEqual(second))
    expect(host.reviewEdits.map((call) => call.request)).toContainEqual(first)
  })

  it('没有 review 就不送（正常打开一份文档）', async () => {
    const view = mount()
    await view.rerender({ document: doc('d1') })
    await waitFor(() => expect(view.container.querySelector('[data-open-document="d1"]')).not.toBeNull())
    expect(host.reviewEdits).toHaveLength(0)
  })
})
