// 设计 token 页：每条 token 一个名字、两个色块、一句「用在哪」。
//
// 这一份守的是**值是现读的**：同一个真 `src/style.css`，换一份值不一样的样式表重挂，
// 页面上跟着换。要是哪天有人图省事在页面里抄一份 token 表，这一条就红。
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { render } from '@testing-library/vue'
import { afterEach, describe, expect, it } from 'vitest'

import { documentTokenScopes, groupColors } from './demoTokens'
import DemoTokensPage from './DemoTokensPage.vue'

const html = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '../../style.css'), 'utf8')

/** 后挂上去的那份样式表：每条 `it` 自己收。 */
let injected: HTMLStyleElement | null = null

function withStyle(text: string): void {
  injected = document.createElement('style')
  injected.textContent = text
  document.head.appendChild(injected)
}

afterEach(() => {
  injected?.remove()
  injected = null
})

describe('设计 token 页', () => {
  it('每种颜色一节：节名和每条的名字都摆出来', () => {
    withStyle(html)
    const expected = groupColors(documentTokenScopes())
    expect(expected.length).toBeGreaterThan(4)

    const view = render(DemoTokensPage)
    const heads = Array.from(view.container.querySelectorAll('.tokens-h2')).map((h) => h.textContent)
    for (const section of expected) {
      expect(heads, section.name).toContain(section.name)
      const text = view.container.textContent ?? ''
      for (const name of section.names.slice(0, 3)) expect(text, name).toContain(name)
    }
    // 色块是成对的：一条浅色、一条深色。
    expect(view.container.querySelectorAll('.swatch-paint').length).toBe(
      2 * expected.reduce((n, s) => n + s.names.length, 0)
    )
  })

  it('值的来源是样式表：换一份不一样的，页面上跟着换', () => {
    withStyle(":root { --ink: #123456; } :root[data-theme='dark'] { --ink: #abcdef; }")
    const view = render(DemoTokensPage)
    const paints = Array.from(view.container.querySelectorAll('.swatch-paint'))
    // 浅色那一格是浅色档的值，深色那一格是深色档解出来的值 —— 不是同一个颜色。
    const colors = paints.map((paint) => (paint as HTMLElement).style.background)
    expect(colors[0]).toContain('#123456')
    expect(colors[1]).toContain('#abcdef')
    expect(view.container.textContent).toContain('--ink')
  })

  it('深色档没重写的那条，两格是同一个值', () => {
    withStyle(':root { --ring: #123456; }')
    const view = render(DemoTokensPage)
    const colors = Array.from(view.container.querySelectorAll('.swatch-paint')).map(
      (paint) => (paint as HTMLElement).style.background
    )
    expect(colors).toHaveLength(2)
    expect(colors[1]).toBe(colors[0])
  })

  it('值读不到时说清楚，不摆一页看着像真值的空壳', () => {
    // 不注样式表：这个环境里读不出任何一条。
    const view = render(DemoTokensPage)
    expect(view.container.querySelector('.tokens-empty')).not.toBeNull()
    expect(view.container.querySelectorAll('.swatch').length).toBe(0)
  })

  it('圆角、字号、动效、层级、字体、页宽那几节都在', () => {
    withStyle(html)
    const view = render(DemoTokensPage)
    const heads = Array.from(view.container.querySelectorAll('.tokens-h2')).map((h) => h.textContent ?? '')
    for (const name of ['圆角与投影', '字号与行高', '动效', '层级', '字体与页宽']) {
      expect(heads, name).toContain(name)
    }
    const text = view.container.textContent ?? ''
    for (const token of ['--radius-md', '--dur-base', '--ease-out', '--font-mono', '--page-w-read', '--z-banner']) {
      expect(text, token).toContain(token)
    }
  })

  it('页宽摆的是定义处那一版，窄视口那一版补在下面一行', () => {
    withStyle(html)
    const view = render(DemoTokensPage)
    const defs = Array.from(view.container.querySelectorAll('.def'))
    const width = defs.find((def) => def.querySelector('.def-name')?.textContent === '--page-w')
    expect(width, '--page-w 那一行').toBeTruthy()
    expect(width?.querySelector('.def-value')?.textContent).toBe('920px')
    // 产品在 959.98px 以下把两条页宽都改成 720：那个值不进上面那一列，但也不该丢。
    expect(width?.querySelector('.def-stack')?.textContent).toContain('720px')
  })

  it('动效那两条真读到了值：时长摆的是毫秒、曲线画得出来', () => {
    withStyle(html)
    const view = render(DemoTokensPage)
    // 曲线是从产品声明的四个数画出来的，不是照一张图描的。
    expect(view.container.querySelectorAll('svg.ease').length).toBe(3)
    expect(view.container.textContent).toContain('220ms')
  })

  it('层级那一梯按数值排，高的在后面', () => {
    withStyle(html)
    const view = render(DemoTokensPage)
    const names = Array.from(view.container.querySelectorAll('.ladder-name')).map((n) => n.textContent ?? '')
    expect(names.length).toBeGreaterThan(10)
    expect(names.indexOf('--z-banner')).toBeGreaterThan(names.indexOf('--z-panel'))
  })
})
