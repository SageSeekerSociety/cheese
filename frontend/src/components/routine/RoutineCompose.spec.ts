// 新建那张表的触发那一段。钉两件事：
//
//   - 点一下选中，再点一下取消；两颗都不勾是允许的（存下来先不跑）。
//   - 不勾触发方式也存得下去：交出去的 `trigger` 是空串，不是替人挑一个。
import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it } from 'vitest'

import RoutineCompose from './RoutineCompose.vue'

import i18n, { setLocale, t } from '@/i18n'

const Compose = RoutineCompose as unknown as Component
const vuetify = createVuetify({ components, directives })

beforeEach(() => setLocale('zh-CN'))

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
})

function open() {
  return render(Compose, {
    props: { modelValue: true, routine: null, rooms: [], defaultRoom: 'room-1', error: '', agentName: '小 C' },
    global: { plugins: [vuetify, i18n] },
  })
}

/** 「时间」那颗勾选框：Vuetify 给它挂了 aria-label。 */
function timeBox(): HTMLInputElement {
  const box = document.querySelector<HTMLInputElement>(
    `input[type=checkbox][aria-label="${t('routines.form.timeOn')}"]`
  )
  if (!box) throw new Error('time checkbox not found')
  return box
}

async function toggleTimeBox(on: boolean) {
  const box = timeBox()
  if (box.checked === on) return
  // Vuetify listens to the input's `input` event; happy-dom's click on a
  // checkbox does not fire one.
  box.checked = on
  await fireEvent.input(box)
}

describe('RoutineCompose trigger', () => {
  it('点两下「时间」就回到两个都不选，并且明说可以不选', async () => {
    const { getByText } = open()
    await toggleTimeBox(true)
    expect(timeBox().checked).toBe(true)

    await toggleTimeBox(false)
    expect(timeBox().checked).toBe(false)
    await waitFor(() => expect(getByText(t('routines.form.triggerUnset'))).toBeTruthy())
  })

  it('两个都不选也存得下去：交出去的 trigger 是空串', async () => {
    const { getByText, getByLabelText, emitted } = open()

    const goal = getByLabelText(t('routines.form.goal'))
    await fireEvent.update(goal, '每周整理一次进展')

    await toggleTimeBox(true)
    await toggleTimeBox(false)

    await fireEvent.click(getByText(t('routines.save')))

    await waitFor(() => expect(emitted().save).toBeTruthy())
    const payload = (emitted().save as unknown[][])[0][0] as {
      body: { trigger: string; spec: Record<string, unknown> }
    }
    expect(payload.body.trigger).toBe('')
    expect(payload.body.spec).toEqual({})
  })
})

// 项目可以给队友改名：表上说到「谁照目标开工」时写它现在的名字，不写默认的「芝士」。
describe('RoutineCompose names the teammate', () => {
  it('改过名的队友：说明里写它的名字', () => {
    const { container } = open()
    const text = container.textContent ?? ''
    expect(text).toContain('由小 C参照目标生成')
    expect(text).not.toContain('芝士')
  })
})
