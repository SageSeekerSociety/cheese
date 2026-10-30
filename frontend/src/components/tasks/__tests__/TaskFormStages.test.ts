import type { TaskRubricLine, TaskStage, TaskSubmissionSchemaEntry } from '@/types'

import { defineComponent, h, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it } from 'vitest'

import TaskFormStages from '../form/TaskFormStages.vue'

import i18n, { setLocale } from '@/i18n'

// 确认框是一个 VOverlay，而 happy-dom 没有 visualViewport：不补这几样，弹窗挂不上去。
beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  if (!globalThis.visualViewport) {
    Object.defineProperty(globalThis, 'visualViewport', {
      configurable: true,
      value: { width: 1024, height: 768, offsetLeft: 0, offsetTop: 0, addEventListener() {}, removeEventListener() {} },
    })
  }
})

beforeEach(() => setLocale('zh-CN'))

function mount(removalRefusal?: (stages: TaskStage[]) => Promise<string | null>) {
  const stages = ref<TaskStage[]>([
    { id: 'need', name: '需求分析', description: '', dueAt: null },
    { id: 'impl', name: '实现', description: '', dueAt: null },
  ])
  const items = ref<TaskSubmissionSchemaEntry[]>([])
  const rubric = ref<TaskRubricLine[]>([])
  const Host = defineComponent({
    setup: () => () =>
      h(components.VApp, null, () =>
        h(TaskFormStages, {
          stages: stages.value,
          'onUpdate:stages': (next: TaskStage[]) => (stages.value = next),
          submissionSchema: items.value,
          'onUpdate:submissionSchema': (next: TaskSubmissionSchemaEntry[]) => (items.value = next),
          rubric: rubric.value,
          'onUpdate:rubric': (next: TaskRubricLine[]) => (rubric.value = next),
          editing: !!removalRefusal,
          removalRefusal,
        })
      ),
  })
  const view = render(Host, { global: { plugins: [createVuetify({ components, directives }), i18n] } })
  return { view, names: () => stages.value.map((stage) => stage.name) }
}

const dialogButton = (label: string) =>
  screen.getAllByRole('button').find((b) => b.textContent?.trim() === label) as HTMLElement

describe('阶段那一节', () => {
  it('删除一个阶段先问一句：取消就还在，答「删除」才删', async () => {
    const { names } = mount()

    await fireEvent.click(screen.getByRole('button', { name: '删除第 1 个阶段' }))
    await waitFor(() => expect(screen.getByRole('alertdialog', { name: '删除第 1 个阶段？' })).toBeTruthy())
    await fireEvent.click(dialogButton('取消'))
    expect(names()).toEqual(['需求分析', '实现'])

    await fireEvent.click(screen.getByRole('button', { name: '删除第 1 个阶段' }))
    await waitFor(() => expect(dialogButton('删除')).toBeTruthy())
    await fireEvent.click(dialogButton('删除'))
    await waitFor(() => expect(names()).toEqual(['实现']))
  })

  it('有人交过的阶段：点删除就说不能删，不弹确认、不删', async () => {
    const { names } = mount(async (rest) =>
      rest.some((stage) => stage.id === 'need') ? null : '阶段「需求分析」已经有人提交，不能删除'
    )

    await fireEvent.click(screen.getByRole('button', { name: '删除第 1 个阶段' }))

    expect((await screen.findByRole('alert')).textContent).toContain('阶段「需求分析」已经有人提交，不能删除')
    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(names()).toEqual(['需求分析', '实现'])

    await fireEvent.click(screen.getByRole('button', { name: '删除第 2 个阶段' }))
    await waitFor(() => expect(screen.getByRole('alertdialog', { name: '删除第 2 个阶段？' })).toBeTruthy())
  })
})
