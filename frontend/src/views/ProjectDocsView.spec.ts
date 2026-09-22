import { defineComponent, h, ref } from 'vue'
import { createVuetify } from 'vuetify'
import { fireEvent, render } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'

vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))
// 这一页的数据源是缓存的，每个用例先摆好它看到的那一份，再渲染。
const state = vi.hoisted(() => ({ payload: {} as Record<string, unknown> }))
vi.mock('@/composables/useCachedResource', () => ({
  useCachedResource: () => ({
    data: ref({
      projectName: '项目',
      rootTopicId: 'root',
      decisions: [],
      weeklies: [],
      memoryEntries: [],
      ...state.payload,
    }),
    loading: ref(false),
    error: ref(null),
  }),
}))
vi.mock('../me', () => ({ myHandle: () => 'writer' }))

import ProjectDocsView from './ProjectDocsView.vue'

describe('周报集', () => {
  it('按窗口列出一份真周报，并指得回它写在哪间房', async () => {
    state.payload = {
      weeklies: [
        {
          id: 'w1',
          topic_id: 'room-1',
          kind: 'weekly',
          content: '本周交付了产物页预览。',
          created_at: '2026-09-07T02:00:00Z',
          meta: { since: '2026-08-31T00:00:00+00:00', until: '2026-09-06T23:59:59+00:00' },
        },
      ],
    }
    const view = render(ProjectDocsView, {
      props: { projectId: 'p', kind: 'weeklies' },
      global: { plugins: [createVuetify()] },
    })
    // 窗口是这一行的身份：并排摆着的几份周报，是它把它们分开的。
    expect(view.getByText('8月31日 – 9月6日')).toBeTruthy()
    expect(view.getByText('本周交付了产物页预览。')).toBeTruthy()
    expect(view.getByText('来自话题')).toBeTruthy()
    view.unmount()
  })

  it('空态说清怎么让芝士写，而不是许一个没人实现的周期', async () => {
    state.payload = {}
    const view = render(ProjectDocsView, {
      props: { projectId: 'p', kind: 'weeklies' },
      global: { plugins: [createVuetify()] },
    })
    expect(view.queryByText('周报由芝士定期产出')).toBeNull()
    expect(view.getByText(/在项目房间里 @ 芝士/)).toBeTruthy()
    expect(view.getByText('暂无周报')).toBeTruthy()
    view.unmount()
  })
})

describe('charter save errors', () => {
  it('keeps the same editor and draft mounted after a conflict, and clears the error after saving', async () => {
    const Editor = defineComponent({
      emits: ['error', 'saved'],
      setup(_props, { emit }) {
        const draft = ref('')
        return () =>
          h('div', [
            h('input', {
              'aria-label': '草稿',
              value: draft.value,
              onInput: (e: Event) => {
                draft.value = (e.target as HTMLInputElement).value
              },
            }),
            h('button', { onClick: () => emit('error', '文档版本冲突') }, '模拟冲突'),
            h('button', { onClick: () => emit('saved') }, '保存成功'),
          ])
      },
    })
    const view = render(ProjectDocsView, {
      props: { projectId: 'p', kind: 'charter' },
      global: { plugins: [createVuetify()], stubs: { DocEditor: Editor } },
    })
    const input = view.getByLabelText('草稿') as HTMLInputElement
    await fireEvent.update(input, '本地未保存的修改')
    await fireEvent.click(view.getByText('模拟冲突'))
    expect(view.getByText('文档版本冲突')).toBeTruthy()
    expect(view.getByLabelText('草稿')).toBe(input)
    expect(input.value).toBe('本地未保存的修改')
    await fireEvent.click(view.getByText('保存成功'))
    expect(view.queryByText('文档版本冲突')).toBeNull()
    view.unmount()
  })
})
