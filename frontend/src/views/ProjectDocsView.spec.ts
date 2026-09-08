import { defineComponent, h, ref } from 'vue'
import { createVuetify } from 'vuetify'
import { fireEvent, render } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'

vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock('@/composables/useCachedResource', () => ({
  useCachedResource: () => ({
    data: ref({ projectName: '项目', rootTopicId: 'root', decisions: [], weeklies: [], memoryEntries: [] }),
    loading: ref(false),
    error: ref(null),
  }),
}))
vi.mock('../me', () => ({ myHandle: () => 'writer' }))

import ProjectDocsView from './ProjectDocsView.vue'

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
