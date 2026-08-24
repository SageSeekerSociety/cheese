// 文件编辑器（Monaco）跟不跟主题走。
//
// 为什么值得单测：Monaco 不是 CSS，它的配色是一份 JS 注册表，主题一换它自己
// 不会变。而这个组件在测试里几乎总是被 mock 掉（PanelDoc 的两个用例都 mock
// 了它），所以「切到深色之后编辑器还是白的」这种事没有任何现成用例会发现。
//
// 盯住两条：
//   1. 挂载时按当前主题定义配色——深色启动不能先画一屏白底；
//   2. 切主题时不但要重新 defineTheme，还必须 setTheme 一次——对着**正在生效**
//      的那个主题名重新 define，Monaco 不会重绘已经在用它的编辑器。
import { createVuetify } from 'vuetify'
import { render } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const defineTheme = vi.fn()
const setTheme = vi.fn()
const create = vi.fn(() => ({
  onDidChangeModelContent: vi.fn(),
  addCommand: vi.fn(),
  getModel: vi.fn(() => null),
  setValue: vi.fn(),
  updateOptions: vi.fn(),
  dispose: vi.fn(),
}))

vi.mock('monaco-editor', () => ({
  editor: { defineTheme, setTheme, create, setModelLanguage: vi.fn() },
  KeyMod: { CtrlCmd: 1 },
  KeyCode: { KeyS: 2 },
}))
// Vite 的 ?worker 导入在 vitest 里没有实现，换成不会被调用的空构造函数。
const worker = { default: class {} }
vi.mock('monaco-editor/esm/vs/editor/editor.worker?worker', () => worker)
vi.mock('monaco-editor/esm/vs/language/css/css.worker?worker', () => worker)
vi.mock('monaco-editor/esm/vs/language/html/html.worker?worker', () => worker)
vi.mock('monaco-editor/esm/vs/language/json/json.worker?worker', () => worker)
vi.mock('monaco-editor/esm/vs/language/typescript/ts.worker?worker', () => worker)

const THEME_STORAGE_KEY = 'cheesex.theme'

/** theme.ts 的偏好是模块级单例，用例之间会串——每次重置模块图重新 import。 */
async function mount() {
  vi.resetModules()
  const { default: CodeEditor } = await import('./CodeEditor.vue')
  const vuetify = createVuetify({ theme: { defaultTheme: 'light', themes: { light: {}, dark: { dark: true } } } })
  return render(CodeEditor, {
    props: { modelValue: 'const a = 1', filename: 'a.ts' },
    global: { plugins: [vuetify] },
  })
}

/** 最后一次 defineTheme 拿到的配色。 */
function lastTheme() {
  return defineTheme.mock.calls.at(-1)?.[1] as { base: string; rules: { token: string; foreground: string }[] }
}

describe('文件编辑器的配色', () => {
  beforeEach(() => {
    localStorage.clear()
    document.documentElement.removeAttribute('data-theme')
    defineTheme.mockClear()
    setTheme.mockClear()
    create.mockClear()
  })

  it('浅色启动时，用浅色底走 vs', async () => {
    localStorage.setItem(THEME_STORAGE_KEY, 'light')
    await mount()
    expect(defineTheme).toHaveBeenCalled()
    expect(lastTheme().base).toBe('vs')
    // 编辑器创建时用的就是这个主题名，不是 Monaco 内置的 vs/vs-dark。
    // mock 没有声明形参，calls 的元素类型是空元组，取下标要先放宽。
    const createOptions = (create.mock.calls[0] as unknown as unknown[])[1]
    expect(createOptions).toMatchObject({ theme: defineTheme.mock.calls[0][0] })
  })

  it('深色启动时就是深色底，不会先白一屏', async () => {
    localStorage.setItem(THEME_STORAGE_KEY, 'dark')
    await mount()
    expect(lastTheme().base).toBe('vs-dark')
  })

  it('挂载后切到深色：重新定义配色，并且重新 setTheme（否则不重绘）', async () => {
    localStorage.setItem(THEME_STORAGE_KEY, 'light')
    await mount()
    const before = defineTheme.mock.calls.length
    setTheme.mockClear()

    const { useAppTheme } = await import('../theme')
    const vuetify = createVuetify({ theme: { defaultTheme: 'light', themes: { light: {}, dark: { dark: true } } } })
    // useAppTheme 要 Vuetify 的注入上下文，借一个最小组件把它跑起来。
    const { createApp, h } = await import('vue')
    let setPreference!: (p: 'system' | 'light' | 'dark') => void
    const host = document.createElement('div')
    createApp({
      setup() {
        setPreference = useAppTheme().setPreference
        return () => h('i')
      },
    })
      .use(vuetify)
      .mount(host)

    setPreference('dark')
    await new Promise((r) => setTimeout(r, 0))

    expect(defineTheme.mock.calls.length).toBeGreaterThan(before)
    expect(lastTheme().base).toBe('vs-dark')
    expect(setTheme).toHaveBeenCalledWith(defineTheme.mock.calls.at(-1)?.[0])
  })

  it('语法色取的是 --code-* 变量，而不是写死在编辑器里', async () => {
    document.documentElement.style.setProperty('--code-keyword', 'rgb(1, 2, 3)')
    localStorage.setItem(THEME_STORAGE_KEY, 'light')
    await mount()
    const keyword = lastTheme().rules.find((r) => r.token === 'keyword')
    expect(keyword?.foreground).toBe('010203')
    document.documentElement.style.removeProperty('--code-keyword')
  })
})
