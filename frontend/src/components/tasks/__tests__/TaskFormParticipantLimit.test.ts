/**
 * 领取人数那颗「不限」勾：勾上 = 这个数不存在。
 *
 * 量的不是那个勾长什么样，而是**交出去的那份 payload**：「不限」这条路必须与「从头
 * 就没填过」是同一件事（`participantLimit` 缺席）。所以这里挂真表单、走真提交
 * （`fireEvent.submit` 与人在页面里敲回车同一条路），拿三条路交出来的那份参数互相比：
 * 勾上「不限」、一开始就没填、以及填了数再勾上 —— 前两条必须一样，第三条必须真的把
 * 那个数送出去。
 *
 * 玩法照 `TaskFormPublishChecks.test.ts`：i18n 键透传（按**标签原文**找字段）、
 * `createVuetify`、stub `ResizeObserver` / `visualViewport`（Vuetify 的浮层定位要读
 * 后者，happy-dom 里没有）。tiptap 那一整块换成壳 —— 与校验无关，真挂起来只是把
 * happy-dom 拖垮。清单那一套（`PUBLISH_CHECKS_SINK`）这里不 provide：表单不 provide
 * 就照旧（见 `TaskForm.vue` 里那一段），这一份量的事与它无关。
 */
import type { Component } from 'vue'

import { nextTick } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
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

/** 三栏必填都填好的样子（其余走 initialValues 的默认值）。 */
const FILLED = { name: '用 gdb 定位一次段错误', submitterType: 'USER', rank: 1, categoryId: 3 }

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

type View = ReturnType<typeof mountForm>

const limitBox = (view: View) => view.getByLabelText('tasks.form.participantLimit') as HTMLInputElement
const unlimitedBox = (view: View) => view.getByRole('checkbox', { name: 'tasks.form.unlimited' }) as HTMLInputElement

/** 点一下那个勾。走**原生 click**：`fireEvent.click` 派发的是合成事件，勾选框的
 *  `checked` 不会跟着翻，Vuetify 那次 `onInput` 读到的就还是旧值。 */
async function toggle(input: HTMLInputElement) {
  input.click()
  await nextTick()
}

/** 交一次，拿回那份 payload。表单自己拦着（校验不过）时提交根本不发生。 */
async function submitted(view: View) {
  await fireEvent.submit(view.container.querySelector('form')!)
  await waitFor(() => expect(view.emitted().submit).toBeTruthy())
  return (view.emitted().submit as unknown[][])[0][0] as { participantLimit?: unknown }
}

describe('发题表单：领取人数「不限」', () => {
  it('新发一道题默认不限：框锁着，交出去不带这一项', async () => {
    const view = mountForm(FILLED)

    expect(unlimitedBox(view).checked).toBe(true)
    expect(limitBox(view).disabled).toBe(true)
    expect((await submitted(view)).participantLimit).toBeUndefined()
  })

  it('取消「不限」就能填：填几就是几；再勾上，数清掉，交出去与从没填过一样', async () => {
    const view = mountForm(FILLED)
    const box = limitBox(view)
    const unlimited = unlimitedBox(view)

    await toggle(unlimited)
    expect(box.disabled).toBe(false)
    await fireEvent.update(box, '5')

    await toggle(unlimited)
    expect(box.disabled).toBe(true)
    expect(box.value).toBe('')
    expect((await submitted(view)).participantLimit).toBeUndefined()
  })

  it('取消「不限」填一个数：交出去就是它', async () => {
    const view = mountForm(FILLED)

    await toggle(unlimitedBox(view))
    await fireEvent.update(limitBox(view), '7')

    expect((await submitted(view)).participantLimit).toBe(7)
  })

  it('改一道上限为空的题：打开就是勾上的，交出去不带这一项', async () => {
    const view = mountForm({ ...FILLED, participantLimit: null }, true)

    expect(unlimitedBox(view).checked).toBe(true)
    expect(limitBox(view).disabled).toBe(true)
    expect((await submitted(view)).participantLimit).toBeUndefined()
  })

  it('改一道有上限的题：打开时不勾，那个数摆在那里，交出去还是它', async () => {
    const view = mountForm({ ...FILLED, participantLimit: 3 }, true)

    expect(unlimitedBox(view).checked).toBe(false)
    expect(limitBox(view).value).toBe('3')
    expect((await submitted(view)).participantLimit).toBe(3)
  })
})
