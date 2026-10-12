// 挑人那一件：只画候选、只改「勾了谁」，一个请求都不发。谁在名单上、什么时候发请求，
// 是调用方（TopicMembers）的事 —— 这里测的就是「画得像名册里那一行」和「点一下只改选中」。
import type { Component } from 'vue'
import type { MemberCandidate } from '@/lib/memberPicker'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it } from 'vitest'

import MemberPicker from './MemberPicker.vue'

import i18n, { setLocale } from '@/i18n'

const Picker = MemberPicker as unknown as Component

const OPTIONS: MemberCandidate[] = [
  { handle: 'alice', name: 'Alice', avatarId: null, agent: false },
  { handle: 'bobby', name: '波比', avatarId: 12, agent: false, external: true },
  { handle: 'cheese-t1', name: '芝士', avatarId: null, agent: true, sub: 'cheese' },
]

function mount(props: Record<string, unknown>) {
  return render(Picker, {
    props: { options: OPTIONS, modelValue: [], ...props },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
})

beforeEach(() => {
  setLocale('zh-CN')
  document.body.innerHTML = ''
})

function rows(): HTMLElement[] {
  return Array.from(document.querySelectorAll<HTMLElement>('.member-picker__row'))
}
function rowOf(text: string): HTMLElement {
  return rows().find((r) => r.textContent?.includes(text))!
}

describe('MemberPicker', () => {
  it('每行都像名册里那一行：头像、名字、@handle、标', () => {
    mount({})
    expect(rows()).toHaveLength(3)
    // 头像：没挑过的是彩色首字母，挑过的是本人那张图。
    expect(rowOf('Alice').querySelector('.user-avatar-char')).not.toBeNull()
    expect(rowOf('波比').querySelector('img')?.getAttribute('src')).toContain('/avatars/12')
    // 队友画超椭圆、挂「AI 队友」，名字下面写它自己的 handle（不是座位账号）。
    expect(rowOf('芝士').querySelector('svg.cheese-avatar')).not.toBeNull()
    expect(rowOf('芝士').textContent).toContain('AI 队友')
    expect(rowOf('芝士').querySelector('.identity-view__handle')?.textContent).toBe('@cheese')
    // 被邀请进来的外部成员挂「外部」。
    expect(rowOf('波比').textContent).toContain('外部')
  })

  it('搜索按名字或 @handle 筛，不改候选本身', async () => {
    const utils = mount({})
    await fireEvent.update(document.querySelector('.member-picker__search input')!, 'bob')
    expect(rows().map((r) => r.textContent)).toEqual([expect.stringContaining('波比')])
    expect(utils.emitted('update:modelValue')).toBeUndefined()
  })

  it('一句都没匹配上：说一句没有匹配的成员，不整块收起来', async () => {
    mount({})
    await fireEvent.update(document.querySelector('.member-picker__search input')!, 'zzz')
    expect(rows()).toHaveLength(0)
    expect(document.querySelector('.member-picker__empty')?.textContent).toContain('没有匹配的成员')
  })

  it('点一行：只把这个人加进选中发上去，不点别人', async () => {
    const utils = mount({})
    await fireEvent.click(rowOf('Alice'))
    expect(utils.emitted('update:modelValue')?.at(-1)).toEqual([['alice']])
  })

  it('再点一次同一行：把人从选中里拿掉', async () => {
    const utils = mount({ modelValue: ['alice'] })
    await fireEvent.click(rowOf('Alice'))
    expect(utils.emitted('update:modelValue')?.at(-1)).toEqual([[]])
  })

  it('勾了谁挂在上面当可移除的 chip，× 带名字的可读标签', async () => {
    const utils = mount({ modelValue: ['alice', 'bobby'] })
    const chips = Array.from(document.querySelectorAll('.member-picker__chip'))
    expect(chips).toHaveLength(2)
    expect(chips.map((c) => c.textContent)).toEqual([expect.stringContaining('Alice'), expect.stringContaining('波比')])
    const remove = chips[0].querySelector('.member-picker__unpick')!
    expect(remove.getAttribute('aria-label')).toContain('Alice')
    await fireEvent.click(remove)
    expect(utils.emitted('update:modelValue')?.at(-1)).toEqual([['bobby']])
  })

  it('勾过的人不在候选里了（名册变了）：那个 chip 不画，也不动选中', () => {
    // 勾了 alice 和已不在候选里的 ghost：只画还认得的那一颗，ghost 留着等调用方定夺。
    mount({ modelValue: ['alice', 'ghost'] })
    const chips = Array.from(document.querySelectorAll('.member-picker__chip'))
    expect(chips).toHaveLength(1)
    expect(chips[0].textContent).toContain('Alice')
  })

  it('忙着（正在发请求）：整个不可改，点也不发事件', async () => {
    const utils = mount({ disabled: true })
    expect(document.querySelector<HTMLInputElement>('.member-picker__search input')?.disabled).toBe(true)
    expect(rows()[0].getAttribute('aria-pressed')).toBe('false')
    await fireEvent.click(rowOf('Alice'))
    expect(utils.emitted('update:modelValue')).toBeUndefined()
  })

  it('候选空着（名册没读到）：一个人都列不出来', () => {
    mount({ options: [] })
    expect(rows()).toHaveLength(0)
  })

  it('搜索框有可读的名字，不靠占位符', () => {
    mount({})
    expect(document.querySelector('.member-picker__search input')?.getAttribute('aria-label')).toContain('挑')
  })
})
