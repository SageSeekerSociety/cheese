// UserRef 是展示组件：不装 pinia、不装路由，只给 props 就画得出来（头像画成
// UserAvatar/CheeseAvatar，人那支落到 Vuetify 的 v-avatar 上，所以要挂 Vuetify 插件；
// 除此之外不需要任何 store / 路由）。
// 「点下去到哪」在 UserRefLink.spec.ts 里测（那只容器把 store + 路由接上）。
import type { Component } from 'vue'

import { defineComponent, h } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeAll, describe, expect, it, vi } from 'vitest'

import UserRef from './UserRef.vue'

// 一颗真去处的 chip。地址本身是什么不重要——展示组件不认识路由，只认「有没有 to」。
const TO = { name: 'member', params: { projectId: 'p1', handle: 'alice' } }

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

function mount(props: Record<string, unknown>, onNavigate?: () => void) {
  return render(UserRef as unknown as Component, {
    props,
    ...(onNavigate ? { attrs: { onNavigate } } : {}),
    // 只给 Vuetify：能渲染出来就是这个组件「不需要 store / 路由」的证据。
    global: { plugins: [vuetify] },
  })
}

const chipOf = (el: Element) => el.querySelector<HTMLElement>('.mention')

describe('UserRef：只凭 props 画一个人（头像 + @名字）', () => {
  it('没有 store、没有路由也能画：头像 + @名字，加全局 .mention 样式', () => {
    const { container } = mount({ handle: 'alice', name: '爱丽丝', to: TO })
    const chip = chipOf(container)!
    expect(chip.className).toContain('mention')
    expect(chip.dataset.handle).toBe('alice')
    expect(chip.textContent).toContain('@爱丽丝')
    // 人：画彩色首字母（UserAvatar 的降级）。
    expect(chip.querySelector('.user-avatar-char')?.textContent).toBe('爱')
  })

  it('不传名字就画 handle', () => {
    const { container } = mount({ handle: 'alice' })
    expect(chipOf(container)!.textContent).toContain('@alice')
  })

  it('没有 handle、也没有名字就什么都不画', () => {
    const { container } = mount({})
    expect(chipOf(container)).toBeNull()
  })

  it('handle 是队友的（cheese-*）：画 CheeseAvatar，不画真人头像', () => {
    const { container } = mount({ handle: 'cheese-kimi', name: '芝士' })
    const chip = chipOf(container)!
    expect(chip.querySelector('svg.cheese-avatar')).not.toBeNull()
    expect(chip.querySelector('.user-avatar-char')).toBeNull()
  })

  it('显式 agent=false 的人，哪怕 handle 长得像队友，也画真人头像', () => {
    const { container } = mount({ handle: 'cheese-kimi', name: '芝士', agent: false })
    const chip = chipOf(container)!
    expect(chip.querySelector('.user-avatar-char')).not.toBeNull()
    expect(chip.querySelector('svg.cheese-avatar')).toBeNull()
  })

  it('有 to：是可点的链接（role=link + 能聚焦），点一下发 navigate', async () => {
    const onNavigate = vi.fn()
    const { container } = mount({ handle: 'alice', name: '爱丽丝', to: TO }, onNavigate)
    const chip = chipOf(container)!
    expect(chip.getAttribute('role')).toBe('link')
    expect(chip.getAttribute('tabindex')).toBe('0')
    await fireEvent.click(chip)
    expect(onNavigate).toHaveBeenCalledTimes(1)
  })

  it('聚焦后回车同样发 navigate', async () => {
    const onNavigate = vi.fn()
    const { container } = mount({ handle: 'alice', name: '爱丽丝', to: TO }, onNavigate)
    await fireEvent.keyDown(chipOf(container)!, { key: 'Enter' })
    expect(onNavigate).toHaveBeenCalledTimes(1)
  })

  it('没有 to：照样是 @名字，但不是链接，点了也不发 navigate', async () => {
    const onNavigate = vi.fn()
    const { container } = mount({ handle: 'alice', name: '爱丽丝' }, onNavigate)
    const chip = chipOf(container)!
    expect(chip.getAttribute('role')).toBeNull()
    expect(chip.getAttribute('tabindex')).toBeNull()
    expect(chip.className).toContain('mention--static')
    await fireEvent.click(chip)
    expect(onNavigate).not.toHaveBeenCalled()
  })

  it('没有 handle（只知道名字）：画得出来，但没有去处', () => {
    const { container } = mount({ name: '爱丽丝' })
    const chip = chipOf(container)!
    expect(chip.getAttribute('role')).toBeNull()
    expect(chip.className).toContain('mention--static')
  })

  it('点人名不会同时触发所在那一行自己的点击', async () => {
    const onRow = vi.fn()
    const onNavigate = vi.fn()
    const Row = defineComponent({
      render: () => h('div', { onClick: onRow }, [h(UserRef, { handle: 'alice', name: '爱丽丝', to: TO, onNavigate })]),
    })
    const { container } = render(Row, { global: { plugins: [vuetify] } })
    await fireEvent.click(chipOf(container)!)
    expect(onNavigate).toHaveBeenCalledTimes(1)
    expect(onRow).not.toHaveBeenCalled()
  })
})
