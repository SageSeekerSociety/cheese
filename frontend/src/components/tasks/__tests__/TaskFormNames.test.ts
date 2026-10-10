/**
 * 发题表单上每一格在读屏里叫它自己的名字（标签上写的那个），不叫控件自带的那句话。
 *
 * 玩法照 `TaskFormParticipantLimit.test.ts`：i18n 键透传，按标签原文找字段。
 */
import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

vi.mock('vue-i18n', async (importOriginal) => {
  const actual = await importOriginal<typeof import('vue-i18n')>()
  return { ...actual, useI18n: () => ({ t: (key: string) => key }) }
})

vi.mock('@/components/common/Editor/TipTapEditor.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    default: defineComponent({
      name: 'TipTapEditorStub',
      setup(_, { expose }) {
        expose({ editor: { getText: () => '正文（测试）。' } })
        return () => h('div', { class: 'tiptap-editor' })
      },
    }),
  }
})

import TaskForm from '../TaskForm.vue'

import i18n from '@/i18n'

beforeAll(() => {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    pageLeft: 0,
    pageTop: 0,
    scale: 1,
    addEventListener() {},
    removeEventListener() {},
  })
})

afterEach(cleanup)

function mountForm(initialData: Record<string, unknown> = {}, isEditing = false) {
  return render(TaskForm as Component, {
    props: {
      initialData,
      isEditing,
      classificationTopics: [],
      categories: [{ id: 3, name: '课程作业', displayOrder: 0 }],
    },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

describe('发题表单：读屏念出来的名字', () => {
  it('分类那一格叫「分类」，不叫下拉自带的「打开」', () => {
    const view = mountForm()

    // 焦点落在下拉里的那个输入框上，读屏念的是它的名字。
    const input = view.getByRole('textbox', { name: 'tasks.form.category' })
    expect(input.closest('[role="combobox"]')).not.toBeNull()
    expect(view.queryByRole('textbox', { name: /^(open|打开)$/i })).toBeNull()
  })
})
