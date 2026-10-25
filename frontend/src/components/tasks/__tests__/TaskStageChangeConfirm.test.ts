// 改题页保存前那一句：改阶段会让人被判未通过或回到进行中时，先说几个人，出题人答
// 「保存更改」才存；取消就什么也不存。没人受影响就直接存，不问。
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
function mount(answer: StageChangeEffect) {
  let saved = 0
  let save: () => Promise<void> = async () => {}
  const Host = defineComponent({
    setup() {
      const check = useStageChangeCheck(async () => answer)
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

const button = (label: string) =>
  screen.getAllByRole('button').find((b) => b.textContent?.trim() === label) as HTMLElement

describe('改阶段之前的那一句', () => {
  it('说清几个人会被判未通过、几个人回到进行中；取消不存，答「保存更改」才存', async () => {
    const { save, saved } = mount({ failing: 2, reopening: 1 })

    const first = save()
    const dialog = await screen.findByRole('alertdialog')
    expect(dialog.textContent).toContain('2 位参与者有已过截止、还没提交的阶段，保存后将判为未通过')
    expect(dialog.textContent).toContain('1 位参与者将从未通过回到进行中')
    await fireEvent.click(button('取消'))
    await first
    expect(saved()).toBe(0)

    const second = save()
    await waitFor(() => expect(button('保存更改')).toBeTruthy())
    await fireEvent.click(button('保存更改'))
    await second
    expect(saved()).toBe(1)
  })

  it('没有人受影响：直接存，不问', async () => {
    const { save, saved } = mount({ failing: 0, reopening: 0 })

    await save()

    expect(saved()).toBe(1)
    expect(screen.queryByRole('alertdialog')).toBeNull()
  })
})
