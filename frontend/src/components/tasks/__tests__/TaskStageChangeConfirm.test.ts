// 改题页保存前那一句：改阶段会让人被判未通过或回到进行中时，先说是谁、因为哪个阶段，
// 出题人答「保存更改」才存；取消就什么也不存。没人受影响就直接存，不问。
import type { StageChangeEffect } from '@/views/tasks/composables/useStageChangeCheck'

import { defineComponent, h } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it } from 'vitest'

import TaskStageChangeConfirm from '../TaskStageChangeConfirm.vue'

import i18n, { setLocale } from '@/i18n'
import { useStageChangeCheck } from '@/views/tasks/composables/useStageChangeCheck'

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

/** 页面那样用：保存先过 `allows`，过了才存。 */
function mount(answer: StageChangeEffect | Error) {
  let saved = 0
  let save: () => Promise<void> = async () => {}
  const Host = defineComponent({
    setup() {
      const check = useStageChangeCheck(async () => {
        if (answer instanceof Error) throw answer
        return answer
      })
      save = async () => {
        if (await check.allows([])) saved++
      }
      return () =>
        h(components.VApp, null, () =>
          h(TaskStageChangeConfirm, { effect: check.effect.value, onAnswer: check.answer })
        )
    },
  })
  render(Host, { global: { plugins: [createVuetify({ components, directives }), i18n] } })
  return { save: () => save(), saved: () => saved }
}

const nobody = { failingNames: [], reopeningNames: [], failingStages: [] }

const button = (label: string) =>
  screen.getAllByRole('button').find((b) => b.textContent?.trim() === label) as HTMLElement

describe('改阶段之前的那一句', () => {
  it('说清谁会被判未通过、因为哪个阶段，谁回到进行中；取消不存，答「保存更改」才存', async () => {
    const { save, saved } = mount({
      failing: 2,
      reopening: 1,
      failingNames: ['爱丽丝', '第 4 组'],
      reopeningNames: ['鲍勃'],
      failingStages: ['需求分析'],
    })

    const first = save()
    const dialog = await screen.findByRole('alertdialog')
    expect(dialog.textContent).toContain('「需求分析」已过截止，爱丽丝、第 4 组还没提交，保存后将判为未通过')
    expect(dialog.textContent).toContain('鲍勃将从未通过回到进行中')
    await fireEvent.click(button('取消'))
    await first
    expect(saved()).toBe(0)

    const second = save()
    await waitFor(() => expect(button('保存更改')).toBeTruthy())
    await fireEvent.click(button('保存更改'))
    await second
    expect(saved()).toBe(1)
  })

  it('阶段在句首，人在后；人多时只点前几个名字，后面写「等 N 人」', async () => {
    const names = ['甲', '乙', '丙', '丁', '戊', '己', '庚']
    const { save } = mount({
      ...nobody,
      failing: 7,
      reopening: 0,
      failingNames: names,
      failingStages: ['需求分析', '实现'],
    })

    void save()
    const dialog = await screen.findByRole('alertdialog')
    expect(dialog.textContent).toContain('「需求分析」、「实现」已过截止，甲、乙、丙、丁、戊等 7 人还没提交')
    expect(dialog.textContent).not.toContain('己')
  })

  it('没有人受影响：直接存，不问', async () => {
    const { save, saved } = mount({ ...nobody, failing: 0, reopening: 0 })

    await save()

    expect(saved()).toBe(1)
    expect(screen.queryByRole('alertdialog')).toBeNull()
  })

  it('保存会被拒的修改：不弹人数，拿到的是保存那句拒绝，什么也不存', async () => {
    const refusal = new Error('阶段「需求分析」已有提交，不能删除')
    const { save, saved } = mount(refusal)

    await expect(save()).rejects.toBe(refusal)

    expect(saved()).toBe(0)
    expect(screen.queryByRole('alertdialog')).toBeNull()
  })
})
