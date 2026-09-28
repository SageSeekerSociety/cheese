/**
 * 发题页右栏那张「提交前」清单，量的是**真表单**。
 *
 * 清单里的规则不是另写一套：`lib/taskPublishChecks.ts` 那份规则表由这张表单自己按
 * `useForm` 的 `values` 现算现报（`watch(values, …, { deep: true, immediate: true })`），
 * 所以这一份测的是「表单真的会拦的都报出来了、不拦的一条都不报」—— 挂的是真
 * `TaskForm.vue`，输入的是真字段，读的是它报上来的那一份。
 *
 * 两件事分开钉：
 *
 * 1. **报的是什么**：一份合规模拟值报空；三栏必填没选时各报一条，且随着输入实时变。
 * 2. **交出去的是真提交**：清单那颗「提交审核」按钮走的是表单交上来的 `submitForm`
 *    （`handleSubmit` 那一支），所以校验不过时点它什么都不发生、过了才 `emit('submit')`。
 *
 * 玩法照 `views/admin/AdminModelsPage.spec.ts`：`useI18n` 键透传（所以按**键名**找
 * 字段）、`createVuetify`、stub `ResizeObserver` / `visualViewport`（Vuetify 的浮层
 * 定位要读后者，happy-dom 里没有）。tiptap 那一大块换成壳 —— 它跟校验无关，真的挂
 * 起来只是把 happy-dom 拖垮。
 */
import type { Component } from 'vue'
import type { PublishCheck, PublishChecksSink } from '@/lib/taskPublishChecks'

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

import { PUBLISH_CHECKS_SINK } from '@/lib/taskPublishChecks'

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

/** 假的外壳：把表单报上来的每一份都收着，最后一份就是「现在」。 */
function makeSink() {
  const state: { last: PublishCheck[] | null; submit: (() => void) | null } = { last: null, submit: null }
  const sink: PublishChecksSink = {
    report: (checks) => {
      state.last = checks
    },
    handOverSubmit: (submit) => {
      state.submit = submit
    },
  }
  return {
    sink,
    state,
    /** 报上来的那几条的 id，按次序。 */
    ids: () => (state.last ?? []).map((check) => check.id),
  }
}

type Harness = ReturnType<typeof makeSink>

function mountForm(harness: Harness, initialData: Record<string, unknown> = {}) {
  const view = render(TaskForm as Component, {
    props: { submitButtonText: '提交', initialData },
    global: {
      plugins: [createVuetify({ components, directives })],
      provide: { [PUBLISH_CHECKS_SINK as symbol]: harness.sink },
    },
  })
  return view
}

/** 表单装好、报上来第一份。 */
async function mounted(harness: Harness, initialData: Record<string, unknown> = {}) {
  const view = mountForm(harness, initialData)
  await waitFor(() => expect(harness.state.last).not.toBeNull())
  return view
}

/** 三栏必填都填好的样子（其余走 initialValues 的默认值）。 */
const FILLED = { name: '用 gdb 定位一次段错误', submitterType: 'USER', rank: 1, categoryId: 3 }

describe('发题表单 →「提交前」清单', () => {
  it('三栏必填都选好：一条都不报', async () => {
    const harness = makeSink()
    await mounted(harness, FILLED)

    expect(harness.ids()).toEqual([])
  })

  it('刚打开（三栏必填全空）：报的就是这三栏 + 标题', async () => {
    const harness = makeSink()
    await mounted(harness, {})

    expect(harness.ids()).toEqual(['name', 'submitterType', 'rank', 'categoryId'])
    // 报的每一句都是人话，不是字段名。
    expect(harness.state.last?.map((check) => check.text).join(' ')).toContain('标题')
  })

  it('标题一边敲一边报：空着报、填上就不报、清掉又报', async () => {
    const harness = makeSink()
    const view = await mounted(harness, FILLED)

    await fireEvent.update(view.getByLabelText('tasks.form.taskName'), '')
    await waitFor(() => expect(harness.ids()).toEqual(['name']))

    await fireEvent.update(view.getByLabelText('tasks.form.taskName'), '缓存')
    await waitFor(() => expect(harness.ids()).toEqual([]))

    await fireEvent.update(view.getByLabelText('tasks.form.taskName'), 'x'.repeat(101))
    await waitFor(() => expect(harness.ids()).toEqual(['name']))
  })

  it('讲解视频：http 链接被拦，https 放行（同一个字段上的一来一回）', async () => {
    const harness = makeSink()
    const view = await mounted(harness, FILLED)

    await fireEvent.update(view.getByLabelText('视频链接（选填）'), 'http://example.com/video')
    await waitFor(() => expect(harness.ids()).toEqual(['videoUrl']))

    await fireEvent.update(view.getByLabelText('视频链接（选填）'), 'https://www.bilibili.com/video/BV1xx411c7mD')
    await waitFor(() => expect(harness.ids()).toEqual([]))
  })

  it('参与人数上限：填 0 被拦，填上 ≥ 1 的整数放行（清单空的那一刻表单也真交得出去）', async () => {
    const harness = makeSink()
    const view = await mounted(harness, FILLED)
    const box = view.getByLabelText('参与者人数限制')

    await fireEvent.update(box, '0')
    await waitFor(() => expect(harness.ids()).toEqual(['participantLimit']))
    // 清单说「拦着」＝表单自己也不放行：这会儿交，一个 submit 都不发。
    harness.state.submit?.()
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(view.emitted().submit).toBeUndefined()

    await fireEvent.update(box, '5')
    await waitFor(() => expect(harness.ids()).toEqual([]))
    harness.state.submit?.()
    await waitFor(() => expect(view.emitted().submit).toBeTruthy())
    // `.number` 是真生效的：交出去的是数字 5，不是字符串 '5'（`TaskForm.vue:889`
    // 原样把 `participantLimit.value` 递出去，所以这里能看见它到底是什么）。
    const payload = (view.emitted().submit as unknown[][])[0][0] as { participantLimit: unknown }
    expect(payload.participantLimit).toBe(5)
  })

  it('参与人数上限：删空之后拿到的是空串、表单照样拦（所以清单也照样报）', async () => {
    const harness = makeSink()
    const view = await mounted(harness, FILLED)
    const box = view.getByLabelText('参与者人数限制')

    await fireEvent.update(box, '12')
    await waitFor(() => expect(harness.ids()).toEqual([]))

    await fireEvent.update(box, '')
    await waitFor(() => expect(harness.ids()).toEqual(['participantLimit']))
    harness.state.submit?.()
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(view.emitted().submit).toBeUndefined()
  })

  it('「提交审核」那颗按钮走的是真提交：拦着的时候点它什么都不发生，放行了才交出去', async () => {
    const harness = makeSink()
    const view = await mounted(harness, { ...FILLED, name: '' })

    // 表单把提交交上来了（清单那颗按钮点下去就是调它）。
    expect(harness.state.submit).toBeTypeOf('function')

    // 还拦着：`handleSubmit` 校验不过，一个 submit 都不发。
    harness.state.submit?.()
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(view.emitted().submit).toBeUndefined()

    // 填上标题：清单空了，这一次真的发出去了，交的是表单自己那份 payload。
    await fireEvent.update(view.getByLabelText('tasks.form.taskName'), '缓存')
    await waitFor(() => expect(harness.ids()).toEqual([]))
    harness.state.submit?.()
    await waitFor(() => expect(view.emitted().submit).toBeTruthy())
    const payload = (view.emitted().submit as unknown[][])[0][0] as { name: string }
    expect(payload.name).toBe('缓存')
  })

  it('表单卸了就把提交收回去（清单那颗按钮不会再打到一具空壳上）', async () => {
    const harness = makeSink()
    const view = await mounted(harness, FILLED)

    view.unmount()

    expect(harness.state.submit).toBeNull()
  })
})
