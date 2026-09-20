// 这一组守的是**抽屉在窄屏上的两处静默失效**：第一处（关着的抽屉盖住整屏）是「看着
// 正常但没反应」，第二处（附件按钮）是「看着能点但什么都不发生」——两个都不报错，都
// 只能靠断言钉住。
import type { Component } from 'vue'

import { h } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import { VApp, VMain } from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { describe, expect, it, vi } from 'vitest'

import SubmitFeedbackDrawer from './SubmitFeedbackDrawer.vue'

const Drawer = SubmitFeedbackDrawer as unknown as Component

// 抽屉要 v-app provide 的 layout，所以外面得套一层——这也正是它在真实应用里的样子。
const Harness = {
  render: () => h(VApp, null, { default: () => h(VMain, null, { default: () => h(Drawer) }) }),
}

// 手机宽度必须**在建 vuetify 之前**就位：useDisplay 的宽度是 createDisplay 时读一次
// window.innerWidth 存进 shallowRef 的，之后再改这个值它不会跟。桌面宽度下这些坑一个
// 都不出现，所以替身必须钉在窄屏上。
function renderOnPhone() {
  const original = window.innerWidth
  Object.defineProperty(window, 'innerWidth', { value: 375, configurable: true })
  try {
    const vuetify = createVuetify({ components, directives })
    return render(Harness, { global: { plugins: [vuetify, createPinia()] } })
  } finally {
    Object.defineProperty(window, 'innerWidth', { value: original, configurable: true })
  }
}

describe('提交反馈抽屉', () => {
  it('关着的时候必须真的移出屏幕——宽度给百分比会让 transform 变成 NaN 被丢掉', () => {
    const { container } = renderOnPhone()
    const drawer = container.querySelector('.v-navigation-drawer') as HTMLElement | null
    expect(drawer, '抽屉没挂出来').toBeTruthy()

    // 宽度给 '100%' 时 VNavigationDrawer 用 Number(props.width) 收下它，得到 NaN；
    // NaN 走到 translateX(...) 里就不是合法 CSS，浏览器把整条 transform 丢掉。少了
    // 这条 transform，「关着」的抽屉根本没有移出屏幕，它整屏压在页面上，底栏正好盖住
    // 输入框那一行——点击全被它接走。宽度的具体数值不重要，这条 transform 在才重要。
    const transform = drawer!.style.transform
    expect(transform, '关着的抽屉没有 transform，它就停在屏幕上盖住页面').toBeTruthy()
    expect(transform).not.toContain('NaN')
    expect(transform).not.toBe('none')
  })

  // 上一版这里守的是「点『选择文件』真的把点击递给那个藏起来的 input」——一个函数式
  // ref 写成静态的，生产构建里按钮就是死的。**附件上传这一版没有做**（后端没有附件
  // 字段），所以那条断言的对象不存在了：留着它只会逼出一个人造 input。改成守现在这
  // 个决定本身 —— 附件还不能选，而且旁边写着为什么。将来接上附件字段时，那条 ref
  // 的坑会原样回来，这个用例该连同 file input 一起改回去。
  it('附件还不能选：按钮是灰的，旁边写着为什么 —— 一个能点、点了什么都不发生的按钮更糟', () => {
    const { container } = renderOnPhone()

    expect(container.querySelector('input[type=file]'), '这一版没有附件上传，不该有藏着的 file input').toBeNull()

    const button = Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.includes('选择文件')) as
      | HTMLButtonElement
      | undefined
    expect(button, '找不到「选择文件」按钮').toBeTruthy()
    expect(button!.disabled, '它必须点不动：附件字段后端还没有').toBe(true)
    expect(container.textContent, '灰按钮旁边得写着为什么').toContain('上传还没接')
  })
})
