// UserRef 是展示组件：不装 pinia、不装路由、不装 vuetify，只给 props。
// 「点下去到哪」在 UserRefLink.spec.ts 里测（那只容器把 store + 路由接上）。
import type { Component } from 'vue'

import { defineComponent, h } from 'vue'
import { fireEvent, render } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'

import UserRef from './UserRef.vue'

// 一颗真去处的 chip。地址本身是什么不重要——展示组件不认识路由，只认「有没有 to」。
const TO = { name: 'member', params: { projectId: 'p1', handle: 'alice' } }

function mount(props: Record<string, unknown>, onNavigate?: () => void) {
  return render(UserRef as unknown as Component, {
    props,
    ...(onNavigate ? { attrs: { onNavigate } } : {}),
    // 刻意不给任何 plugin：能渲染出来就是这个组件「只靠 props」的证据。
  })
}

describe('UserRef：只凭 props 画一个人名', () => {
  it('没有 store、没有路由也能画：@名字 加全局 .mention 样式', () => {
    const { getByText } = mount({ handle: 'alice', name: '爱丽丝', to: TO })
    const chip = getByText('@爱丽丝')
    expect(chip.className).toBe('mention')
    expect(chip.dataset.handle).toBe('alice')
  })

  it('不传名字就画 handle', () => {
    const { getByText } = mount({ handle: 'alice' })
    expect(getByText('@alice').textContent).toBe('@alice')
  })

  it('没有 handle、也没有名字就什么都不画', () => {
    const { container } = mount({})
    expect(container.querySelector('.mention')).toBeNull()
  })

  it('有 to：是可点的链接（role=link + 能聚焦），点一下发 navigate', async () => {
    const onNavigate = vi.fn()
    const { getByText } = mount({ handle: 'alice', name: '爱丽丝', to: TO }, onNavigate)
    const chip = getByText('@爱丽丝')
    expect(chip.getAttribute('role')).toBe('link')
    expect(chip.getAttribute('tabindex')).toBe('0')
    await fireEvent.click(chip)
    expect(onNavigate).toHaveBeenCalledTimes(1)
  })

  it('聚焦后回车同样发 navigate', async () => {
    const onNavigate = vi.fn()
    const { getByText } = mount({ handle: 'alice', name: '爱丽丝', to: TO }, onNavigate)
    await fireEvent.keyDown(getByText('@爱丽丝'), { key: 'Enter' })
    expect(onNavigate).toHaveBeenCalledTimes(1)
  })

  it('没有 to：照样是 @名字，但不是链接，点了也不发 navigate', async () => {
    const onNavigate = vi.fn()
    const { getByText } = mount({ handle: 'alice', name: '爱丽丝' }, onNavigate)
    const chip = getByText('@爱丽丝')
    expect(chip.getAttribute('role')).toBeNull()
    expect(chip.getAttribute('tabindex')).toBeNull()
    expect(chip.className).toBe('mention mention--static')
    await fireEvent.click(chip)
    expect(onNavigate).not.toHaveBeenCalled()
  })

  it('没有 handle（只知道名字）：画得出来，但没有去处', () => {
    const { getByText } = mount({ name: '爱丽丝' })
    const chip = getByText('@爱丽丝')
    expect(chip.getAttribute('role')).toBeNull()
    expect(chip.className).toBe('mention mention--static')
  })

  it('点人名不会同时触发所在那一行自己的点击', async () => {
    const onRow = vi.fn()
    const onNavigate = vi.fn()
    const Row = defineComponent({
      render: () => h('div', { onClick: onRow }, [h(UserRef, { handle: 'alice', name: '爱丽丝', to: TO, onNavigate })]),
    })
    const { getByText } = render(Row)
    await fireEvent.click(getByText('@爱丽丝'))
    expect(onNavigate).toHaveBeenCalledTimes(1)
    expect(onRow).not.toHaveBeenCalled()
  })
})
