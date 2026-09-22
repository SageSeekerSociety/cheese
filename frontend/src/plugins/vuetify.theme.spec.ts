// 主题契约测试：手写 CSS 引用的 Vuetify 颜色，必须真的被 Vuetify 生成出来。
//
// 起因是一个静默失效的缺陷：styles/common.scss 里的页头暖光晕引用了
// `rgba(var(--v-theme-logo-secondary), .08)`，但主题里从来没定义过 logo-*
// 这两个颜色。CSS 的 `background` 是简写属性，**一层无效整条声明作废**——所以
// 那个三层渐变 + 12 秒流动动画一层都没渲染出来，页面上只剩一条内阴影和一个
// 空转的动画。没有任何报错，没有任何测试会红。
//
// 这里断言的是「变量真的被生成了」，而不是「配置里写了这一行」——后者可以靠读
// 代码确认，前者才是 CSS 实际能不能拿到值。
import { createApp, h } from 'vue'
import { describe, expect, it } from 'vitest'

// 顶层 import，不在用例里 `await import()`：那样编译 Vuetify 和它那套 SCSS 的时间
// 就落在 5 秒的用例超时里，机器一忙就红，而红的原因和它断言的东西毫无关系。这里
// 没有 `resetModules`，所以动态 import 本来也只有第一次真的编译，第二个用例拿的是
// 缓存——换成顶层 import 语义完全一样，只是那份代价移到了收集阶段。
import vuetify from './vuetify'

/**
 * Vuetify 把主题色编译成一段 <style id="vuetify-theme-stylesheet"> 注进 <head>。
 * 必须挂载一个真的 app —— 样式注入发生在插件 install 的响应式作用域里。
 * 这里用产品的真实配置（import './vuetify'），不另写一份，否则测的就不是线上那套。
 */
function generatedThemeCss(): string {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp({ render: () => h('div') })
  app.use(vuetify)
  app.mount(host)
  const css = document.getElementById('vuetify-theme-stylesheet')?.textContent ?? ''
  app.unmount()
  host.remove()
  return css
}

describe('Vuetify 主题变量', () => {
  it('common.scss 引用的 logo-primary / logo-secondary 会被生成成 CSS 变量', () => {
    const css = generatedThemeCss()
    expect(css).toContain('--v-theme-logo-primary')
    expect(css).toContain('--v-theme-logo-secondary')
  })

  it('浅色和深色两套主题都生成了这两个变量', () => {
    const css = generatedThemeCss()
    const light = css.slice(css.indexOf('.v-theme--light'), css.indexOf('.v-theme--dark'))
    const dark = css.slice(css.indexOf('.v-theme--dark'))
    for (const block of [light, dark]) {
      expect(block).toContain('--v-theme-logo-primary')
      expect(block).toContain('--v-theme-logo-secondary')
    }
  })
})

describe('真实主题配置', () => {
  it('两套主题都定义了 common.scss 需要的每一个颜色', () => {
    // 直接读真实配置，防止上面的测试用例和产品配置各写各的而对不上。
    const themes = (vuetify as unknown as { theme: { themes: { value: Record<string, { colors: object }> } } }).theme
      .themes.value

    expect(Object.keys(themes).sort()).toEqual(['dark', 'light'])
    for (const name of ['light', 'dark']) {
      const colors = themes[name].colors as Record<string, string>
      // common.scss 第 83/84/94 行用到的三个。
      for (const key of ['logo-primary', 'logo-secondary', 'primary']) {
        expect(colors[key], `${name} 主题缺少 ${key}`).toBeTruthy()
      }
    }
  })
})
