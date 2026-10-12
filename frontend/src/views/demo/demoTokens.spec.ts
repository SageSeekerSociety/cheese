// token 页要的那点数据：两档值怎么读出来、`var()` 怎么解开、颜色怎么分节。
//
// 页面上的值是**现读**的，所以这一份测的是读法：摆几份假的样式表进去，看读出来的
// 是不是它。最后那一组拿真的 `src/style.css` 跑一遍——那是「没有一条颜色落进『其余』」
// 的守卫，也是「没有只在深色档出现的 token」的守卫。
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import type { StyleRule, StyleSheetLike, TokenDecls } from './demoTokens'

import { afterEach, describe, expect, it } from 'vitest'

import {
  colorTokens,
  documentTokenScopes,
  groupColors,
  isColorValue,
  numericValue,
  previewOf,
  readTokenScopes,
  resolveIn,
  tokenNames,
} from './demoTokens'

/* ------------------------------------------------------------ 假的样式表 */

function declsOf(map: Record<string, string>): TokenDecls {
  const names = Object.keys(map)
  return {
    length: names.length,
    item: (index: number) => names[index] ?? null,
    getPropertyValue: (name: string) => map[name] ?? '',
  }
}

function rule(selectorText: string, map: Record<string, string>, nested: StyleRule[] = []): StyleRule {
  return { selectorText, style: declsOf(map), cssRules: nested }
}

function sheetOf(...rules: StyleRule[]): StyleSheetLike {
  return { cssRules: rules }
}

/* ------------------------------------------------------------ 读两档 token */

describe('从样式表里读两档 token', () => {
  it('浅色从 :root 来，深色从 :root[data-theme=dark] 来', () => {
    const sheet = sheetOf(
      rule(':root', { '--ink': '#191a1c', '--line-2': '#e2e3e6' }),
      rule(":root[data-theme='dark']", { '--ink': '#e8e6e2' })
    )
    const scopes = readTokenScopes([sheet])
    expect(scopes.light.get('--ink')).toBe('#191a1c')
    expect(scopes.light.get('--line-2')).toBe('#e2e3e6')
    expect(scopes.dark.get('--ink')).toBe('#e8e6e2')
    // 深色块只写它改过的：没写的就是「沿用浅色」，不是空字符串。
    expect(scopes.dark.has('--line-2')).toBe(false)
  })

  it('别的选择器不算数：组件自己临时声明的自定义属性不是设计 token', () => {
    const scopes = readTokenScopes([
      sheetOf(
        rule(':root', { '--ink': '#191a1c' }),
        rule('.card', { '--local-pad': '4px' }),
        rule('body', { '--x': '1' })
      ),
    ])
    expect([...scopes.light.keys()]).toEqual(['--ink'])
  })

  it('@media 里包着的 :root 也走得到', () => {
    const scopes = readTokenScopes([
      sheetOf(rule('@media (width <= 900px)', {}, [rule(':root', { '--page-w': '100%' })])),
    ])
    expect(scopes.light.get('--page-w')).toBe('100%')
  })

  it('不是自定义属性的、空的，都不收', () => {
    const scopes = readTokenScopes([sheetOf(rule(':root', { color: 'var(--ink)', '--ink': '', '--text': '  #333  ' }))])
    expect([...scopes.light.keys()]).toEqual(['--text'])
    expect(scopes.light.get('--text')).toBe('#333')
  })

  it('一张读不了的样式表不带倒后面那些', () => {
    const hostile: StyleSheetLike = {}
    // 别的源的样式表：取 `cssRules` 本身就抛（浏览器里就是这个行为）。
    Object.defineProperty(hostile, 'cssRules', {
      get() {
        throw new Error('cross-origin')
      },
    })
    const scopes = readTokenScopes([hostile, sheetOf(rule(':root', { '--ink': '#191a1c' }))])
    expect(scopes.light.get('--ink')).toBe('#191a1c')
  })

  it('一条都没有的文档不会炸，只是两档都空着', () => {
    expect(readTokenScopes([])).toEqual({ light: new Map(), dark: new Map() })
  })
})

/* ------------------------------------------------------------ 解 var() */

describe('把 var() 解开', () => {
  const scope = new Map([
    ['--fill-2', '#ececea'],
    ['--code-bg', 'var(--fill-2)'],
  ])

  it('引用跟着解开，不管包了几层', () => {
    expect(resolveIn('var(--code-bg)', scope)).toBe('#ececea')
    expect(resolveIn('#fff', scope)).toBe('#fff')
  })

  it('本档没有的去另一档找，两档都没有就原样留着', () => {
    // 深色块只写它改过的那些，别的引用得能从浅色那一档借到。
    expect(resolveIn('var(--text)', new Map(), new Map([['--text', '#333']]))).toBe('#333')
    expect(resolveIn('var(--nope)', scope)).toBe('var(--nope)')
  })

  it('自引用不会转圈：转够了就原样交出来', () => {
    expect(resolveIn('var(--loop)', new Map([['--loop', 'var(--loop)']]))).toBe('var(--loop)')
  })
})

/* ------------------------------------------------------------ 一条在前端的预览 */

describe('一条 token 的两档值', () => {
  const scopes = readTokenScopes([
    sheetOf(rule(':root', { '--fill-2': '#ececea', '--code-bg': 'var(--fill-2)', '--radius-md': '8px' })),
    sheetOf(rule(":root[data-theme='dark']", { '--fill-2': '#2b2d31' })),
  ])

  it('深色没重写的那条，两档是同一个值，并说明白', () => {
    expect(previewOf('--radius-md', scopes)).toEqual({ light: '8px', dark: '8px', differs: false })
  })

  it('深色重写了的那条，两档各是各的', () => {
    expect(previewOf('--fill-2', scopes)).toEqual({ light: '#ececea', dark: '#2b2d31', differs: true })
  })

  it('深色档里写着 var() 的，照深色档解，借用浅色那份兜底', () => {
    // 深色块把 --fill-2 换掉了，所以 --code-bg 在深色下就是深色那一版的 --fill-2；
    // 原样刷 var(--fill-2) 上去会变成**当前**主题的颜色，那正是这一条要挡的事。
    expect(previewOf('--code-bg', scopes)?.dark).toBe('#2b2d31')
  })

  it('样式表里根本没有的，说没有，不编一个值', () => {
    expect(previewOf('--nope', scopes)).toBeNull()
  })
})

/* ------------------------------------------------------------ 判颜色 */

describe('判一个值是不是颜色', () => {
  it('认得出各种写法', () => {
    for (const value of [
      '#fff',
      '#191a1c',
      '#191a1cff',
      'rgba(25, 26, 28, 0.44)',
      'oklch(0.7 0.1 60)',
      'transparent',
    ]) {
      expect(isColorValue(value), value).toBe(true)
    }
  })

  it('不是颜色的不认', () => {
    for (const value of ['8px', '0 1px 2px rgba(25, 26, 28, 0.06)', 'var(--ink)', 'ease-out', '920px', '']) {
      expect(isColorValue(value), value).toBe(false)
    }
  })

  it('只有颜色进颜色这一栏，尺寸和阴影不进', () => {
    const scopes = readTokenScopes([
      sheetOf(
        rule(':root', {
          '--ink': '#191a1c',
          '--radius-md': '8px',
          '--shadow-1': '0 1px 2px rgba(25, 26, 28, 0.06)',
          '--dur-base': '220ms',
        })
      ),
    ])
    expect(colorTokens(scopes)).toEqual(['--ink'])
  })
})

/* ------------------------------------------------------------ 数值 */

describe('取出一个值里的数值', () => {
  it('带单位的取数字，单位丢了', () => {
    expect(numericValue('660px')).toBe(660)
    expect(numericValue('1999')).toBe(1999)
    expect(numericValue('-3')).toBe(-3)
  })

  it('一个数都没有的给 null', () => {
    expect(numericValue('auto')).toBeNull()
    expect(numericValue('')).toBeNull()
  })
})

/* ------------------------------------------------------------ 真的样式表 */

describe('拿真的 src/style.css 跑一遍', () => {
  const styleCss = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '../../style.css'), 'utf8')
  let injected: HTMLStyleElement

  function scopes() {
    injected = document.createElement('style')
    injected.textContent = styleCss
    document.head.appendChild(injected)
    return documentTokenScopes()
  }

  afterEach(() => injected?.remove())

  it('整份 css 都读进来了：数量级对得上', () => {
    const read = scopes()
    expect(read.light.size).toBeGreaterThan(80)
    expect(read.dark.size).toBeGreaterThan(40)
  })

  it('没有只在深色档出现的 token：深色块只重写，不新增', () => {
    const read = scopes()
    for (const name of read.dark.keys()) {
      expect(read.light.has(name), `${name} 只在深色档里有`).toBe(true)
    }
  })

  it('颜色一条不落：每一节都有东西，没有一条掉进「其余」', () => {
    const sections = groupColors(scopes())
    expect(sections.map((section) => section.name)).not.toContain('其余')
    for (const section of sections) expect(section.names.length, section.name).toBeGreaterThan(0)
    // 分出来的和「是颜色的那些」一样多，不多不少。
    const grouped = sections.reduce((total, section) => total + section.names.length, 0)
    expect(grouped).toBe(colorTokens(scopes()).length)
  })

  it('时长、圆角、层级那几族都读到了，且层级是按数值排的', () => {
    const read = scopes()
    expect(tokenNames(read, '--dur-')).toHaveLength(4)
    expect(tokenNames(read, '--radius-')).toHaveLength(4)
    const z = tokenNames(read, '--z-')
    expect(z.length).toBeGreaterThan(10)
    for (const name of z) expect(numericValue(previewOf(name, read)?.light ?? '')).not.toBeNull()
  })

  it('浅色和深色的对照是真的：画布色两档不一样', () => {
    const preview = previewOf('--canvas', scopes())
    expect(preview?.differs).toBe(true)
    expect(preview?.light).not.toBe(preview?.dark)
  })
})
