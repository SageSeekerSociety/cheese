/**
 * 注册 / 登录表单底下那两句协议。
 *
 * 两句只有文字不同、长得一模一样，接错一条不会报任何错 —— 只是点「隐私政策」的人
 * 会读到用户协议。所以这里钉的是**每一条各自的 href**，不是「有两条链接」。
 *
 * 另外两件事是这两条链接长在这里才有的，掉了都没人看得出来：
 *
 *   - `target="_blank"`：点协议是新开一页。去掉它会在本页走掉，而表单里填了一半
 *     的邮箱和密码不会自己回来。
 *   - `@click.stop`：链接长在复选框的 label 里（见调用方），不拦住的话点链接会
 *     顺带把「我已阅读并同意」勾上。
 */
import type { Component } from 'vue'

import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { fireEvent, render } from '@testing-library/vue'
import { beforeEach, describe, expect, it } from 'vitest'

import LegalLinks from './LegalLinks.vue'

import { setLocale } from '@/i18n'

const blank = defineComponent({ setup: () => () => h('div') })

/** 协议那两条路由（`router/legal.ts`），演示不了别的：这里要的只是解析得出地址。 */
function mount() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/legal/terms', name: 'LegalTerms', component: blank },
      { path: '/legal/privacy', name: 'LegalPrivacy', component: blank },
      { path: '/:any(.*)*', component: blank },
    ],
  })
  const view = render(LegalLinks as Component, { global: { plugins: [router] } })
  return { ...view, links: Array.from(view.container.querySelectorAll('a')) }
}

beforeEach(() => setLocale('zh-CN'))

describe('协议那两句', () => {
  it('各通到各的地方，不是两句都指着同一页', () => {
    const { container, links } = mount()

    expect(links).toHaveLength(2)
    expect(links[0].textContent?.trim()).toBe('《用户协议》')
    expect(links[0].getAttribute('href')).toBe('/legal/terms')
    expect(links[1].textContent?.trim()).toBe('《隐私政策》')
    expect(links[1].getAttribute('href')).toBe('/legal/privacy')
    // 两句之间的「和」是普通文字，不是第三条链接。
    expect(container.textContent).toContain('和')
  })

  it('新开一页：填了一半的表单不能因此没了', () => {
    const { links } = mount()

    for (const link of links) expect(link.getAttribute('target')).toBe('_blank')
  })

  it('点链接不顺带勾上同意 —— 它拦住了冒泡', async () => {
    // 调用方（注册表单）把这两句放在复选框的 label 里，`@click.stop` 就是为它写的：
    // 拦不住的话点一次协议会顺带把「我已阅读并同意」勾上。
    const { container } = mount()
    let bubbled = false
    container.addEventListener('click', () => {
      bubbled = true
    })

    await fireEvent.click(container.querySelector('a')!)

    expect(bubbled).toBe(false)
  })
})
