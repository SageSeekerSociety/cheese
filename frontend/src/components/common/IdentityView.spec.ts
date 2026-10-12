/** 「这是谁」那个公共入口对读屏的两面：默认这张脸是装饰（名字就在旁边那行里，
 *  别再念一遍），只有显式给了 `alt` 才把它当成这一格的唯一身份读出来。人和队友
 *  两条分支必须一个行为——人这支 (UserAvatar) 一直是这么做的，队友这支要跟上，
 *  否则按钮/链接里的成员名会被读两次。 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, describe, expect, it } from 'vitest'

import IdentityView from './IdentityView.vue'

afterEach(cleanup)

const vuetify = createVuetify({ components, directives })

function mount(props: Record<string, unknown>) {
  return render(IdentityView, { props, global: { plugins: [vuetify] } })
}

describe('IdentityView 对读屏', () => {
  it('队友（默认）：名字在旁，脸是装饰，不重复念', () => {
    const { container, queryByRole } = mount({ handle: 'cheese-01', name: '小苔', agent: true })
    const face = container.querySelector('.cheese-avatar')!
    expect(face.getAttribute('aria-hidden')).toBe('true')
    expect(queryByRole('img', { name: '小苔' })).toBeNull()
    expect(container.textContent).toContain('小苔')
  })

  it('队友（显式 alt）：脸念的是 alt', () => {
    const { getByRole } = mount({ handle: 'cheese-01', name: '小苔', agent: true, alt: '队友：小苔' })
    expect(getByRole('img', { name: '队友：小苔' })).toBeTruthy()
  })

  it('真人（默认）：同样只在旁边念名字，脸是装饰', () => {
    const { container, queryByRole } = mount({ handle: 'alice', name: 'Alice' })
    expect(container.querySelector('.identity-view__face')?.getAttribute('aria-hidden')).toBe('true')
    expect(queryByRole('img', { name: 'Alice' })).toBeNull()
    expect(container.textContent).toContain('Alice')
  })
})
