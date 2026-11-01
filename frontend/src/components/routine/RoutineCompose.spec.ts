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

/** 那颗勾选框：Vuetify 给它挂了 aria-label。 */
function boxOf(label: string): HTMLInputElement {
  const box = document.querySelector<HTMLInputElement>(`input[type=checkbox][aria-label="${label}"]`)
  if (!box) throw new Error(`${label} checkbox not found`)
  return box
}
const timeBox = () => boxOf(t('routines.form.timeOn'))
const conditionBox = () => boxOf(t('routines.form.conditionOn'))

async function toggle(box: () => HTMLInputElement, on: boolean) {
  const el = box()
  if (el.checked === on) return
  // Vuetify listens to the input's `input` event; happy-dom's click on a
  // checkbox does not fire one.
  el.checked = on
  await fireEvent.input(el)
}

describe('RoutineCompose trigger', () => {
  it('点两下「时间」就回到两个都不选：两颗都不勾', async () => {
    open()
    await toggle(timeBox, true)
    expect(timeBox().checked).toBe(true)

    await toggle(timeBox, false)
    expect(timeBox().checked).toBe(false)
    expect(conditionBox().checked).toBe(false)
  })

  // 回归：以前「条件」勾的是「不是时间」，两个都不选时它也亮着，点它只是把空串再写一遍，
  // 界面上取消不掉。
  it('点两下「条件」也回到两个都不选：两颗都不勾', async () => {
    open()
    await toggle(conditionBox, true)
    expect(conditionBox().checked).toBe(true)

    await toggle(conditionBox, false)
    expect(conditionBox().checked).toBe(false)
    expect(timeBox().checked).toBe(false)
  })

  it('时间与条件只能留一个：勾一边，另一边自己灭', async () => {
    open()
    await toggle(timeBox, true)
    await toggle(conditionBox, true)
    expect(conditionBox().checked).toBe(true)
    expect(timeBox().checked).toBe(false)

    await toggle(timeBox, true)
    expect(timeBox().checked).toBe(true)
    expect(conditionBox().checked).toBe(false)
  })

  it('两个都不选也存得下去：交出去的 trigger 是空串', async () => {
    const { getByText, getByLabelText, emitted } = open()

    const goal = getByLabelText(t('routines.form.goal'))
    await fireEvent.update(goal, '每周整理一次进展')

    await toggle(timeBox, true)
    await toggle(timeBox, false)

    await fireEvent.click(getByText(t('routines.save')))

    await waitFor(() => expect(emitted().save).toBeTruthy())
    const payload = (emitted().save as unknown[][])[0][0] as {
      body: { trigger: string; spec: Record<string, unknown> }
    }
    expect(payload.body.trigger).toBe('')
    expect(payload.body.spec).toEqual({})
  })
})

// 「模板」那一页上只有模板自己那一行：表单底下那句解释不该跟过来；没有触发方式的模板
// 只写目标，不另造一句说明。
describe('RoutineCompose templates', () => {
  it('模板那页不摆表单底下那句说明，没触发方式的模板只写目标', async () => {
    const { getByText, container } = render(Compose, {
      props: {
        modelValue: true,
        routine: null,
        rooms: [],
        defaultRoom: 'room-1',
        error: '',
        agentName: '小 C',
        templates: [{ id: 't1', title: '整理周报', goal: '汇总本周各频道进展', trigger: '', spec: {} }],
      },
      global: { plugins: [vuetify, i18n] },
    })
    await fireEvent.click(getByText(t('routines.compose.tab.template')))
    await waitFor(() => expect(container.textContent).toContain('整理周报'))
    expect(container.textContent).toContain('汇总本周各频道进展')
    // 表单里那句解释不该跟到模板页来。
    expect(container.textContent).not.toContain('先存下来')
    expect(container.textContent).not.toContain('什么时候跑')
  })
})

// 目标是唯一必填的一格：标签右上有个星号，标签文字本身还是「目标」。
describe('RoutineCompose goal is required', () => {
  it('标签带星号，控件仍然按「目标」找得到', () => {
    const { getByLabelText, container } = open()
    const goal = getByLabelText(t('routines.form.goal'))
    expect(goal.getAttribute('aria-required')).toBe('true')
    const star = container.querySelector('.compose__req')
    expect(star?.getAttribute('aria-hidden')).toBe('true')
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
