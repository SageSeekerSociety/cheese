import { describe, expect, it } from 'vitest'

import Prism from './prism'

// 回归：这一块曾经由 vite-plugin-prismjs 注入成一串「引用全局 Prism」的脚本，而 prism-core
// 在 Rolldown 下是 CJS、被包成惰性求值 —— 脚本先跑、core 还没跑，整个 chunk 抛
// `Prism is not defined`，凡是加载它的路由（空间待审核、题目答案、Markdown 渲染）都在模块
// 求值时炸掉、整页白屏。这里守住「core 先跑，语法都装好」。
describe('prism loader', () => {
  it('registers the grammars, which only happens if the core ran first', () => {
    expect(Prism.languages.javascript).toBeTruthy()
    expect(Prism.languages.typescript).toBeTruthy()
    expect(Prism.languages.python).toBeTruthy()
    expect(Prism.languages.rust).toBeTruthy()
    expect(Prism.languages.bash).toBeTruthy()
    expect(Prism.languages.markdown).toBeTruthy()
  })

  it('highlights through a loaded grammar', () => {
    const html = Prism.highlight('const answer = 42', Prism.languages.javascript, 'javascript')
    expect(html).toContain('token')
  })
})
