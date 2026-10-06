/**
 * 发题时交出去的题目详情，就是交的那一刻编辑器里的正文：存下的 JSON 是它，题目卡片上
 * 那句简介（`intro`）也是从它念出来的。改到最后一笔才点提交，最后一笔也得在里面。
 *
 * 这里挂的是真编辑器（其余几份表单测试把它换成了壳）。
 */
import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

vi.mock('vue-i18n', async (importOriginal) => {
  const actual = await importOriginal<typeof import('vue-i18n')>()
  return { ...actual, useI18n: () => ({ t: (key: string) => key }) }
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

const FILLED = { name: '用 gdb 定位一次段错误', submitterType: 'USER', rank: 1, categoryId: 3 }

const DESCRIPTION = {
  type: 'doc',
  content: [
    { type: 'heading', attrs: { level: 2 }, content: [{ type: 'text', text: '题目要求' }] },
    { type: 'paragraph', content: [{ type: 'text', text: '找出段错误发生的那一行。' }] },
  ],
}

interface LiveEditor {
  commands: { insertContentAt: (pos: number, content: unknown) => boolean }
  state: { doc: { content: { size: number } } }
}

describe('发题：交出去的正文就是编辑器里的正文', () => {
  it('改完最后一笔就交，存下的 JSON 和简介里都有这一笔', async () => {
    const view = render(TaskForm as Component, {
      props: {
        initialData: { ...FILLED, description: DESCRIPTION },
        isEditing: true,
        classificationTopics: [],
        categories: [{ id: 3, name: '课程作业', displayOrder: 0 }],
      },
      global: { plugins: [createVuetify({ components, directives }), i18n] },
    })

    // 编辑器把自己挂在正文那块 DOM 上；用它代替键盘敲进最后一句。
    const surface = await waitFor(() => {
      const el = view.container.querySelector('.ProseMirror') as (HTMLElement & { editor?: LiveEditor }) | null
      expect(el?.editor).toBeTruthy()
      return el!
    })
    const editor = surface.editor!
    editor.commands.insertContentAt(editor.state.doc.content.size, {
      type: 'paragraph',
      content: [{ type: 'text', text: '提示：先开 core dump。' }],
    })

    await fireEvent.submit(view.container.querySelector('form')!)
    await waitFor(() => expect(view.emitted().submit).toBeTruthy())
    const payload = (view.emitted().submit as unknown[][])[0][0] as { description: string; intro: string }

    const saved = JSON.parse(payload.description) as { content: { content?: { text: string }[] }[] }
    const paragraphs = saved.content.map((block) => (block.content ?? []).map((t) => t.text).join(''))
    expect(paragraphs).toEqual(['题目要求', '找出段错误发生的那一行。', '提示：先开 core dump。'])
    expect(payload.intro).toContain('题目要求')
    expect(payload.intro).toContain('找出段错误发生的那一行。')
    expect(payload.intro).toContain('提示：先开 core dump。')
  })
})
