/// <reference types="node" />
// AI 队友的头像：一个项目里的几个队友脸一样，靠底色分开。这里守三件事：
//   * 头像读得出是哪一位队友（读屏读到的是它的名字）；
//   * 底色跟着队友走，不跟着名字走——改了名，颜色不能变；
//   * 每一档底色上的眼睛，两套主题下都看得清。
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { render } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import { AGENT_TONES } from '../utils/avatar'

import CheeseAvatar from './CheeseAvatar.vue'

function toneOf(props: Record<string, unknown>): string | null {
  const { container } = render(CheeseAvatar, { props })
  return container.querySelector('.cheese-avatar')?.getAttribute('data-tone') ?? null
}

describe('CheeseAvatar', () => {
  it('读屏读到的是这位队友的名字', () => {
    const { getByRole } = render(CheeseAvatar, { props: { name: '审稿', handle: 'cheese-3f2a' } })
    expect(getByRole('img', { name: '审稿' })).toBeTruthy()
  })

  it('队友改了名，头像颜色不变', () => {
    const before = toneOf({ name: '芝士', handle: 'cheese-3f2a' })
    const after = toneOf({ name: '审稿助手', handle: 'cheese-3f2a' })
    expect(before).not.toBeNull()
    expect(after, '颜色跟着名字变了：改名之后，房间里看起来像换了一位队友').toBe(before)
  })

  it('不同的队友分到不同的颜色', () => {
    const tones = new Set(
      Array.from({ length: 40 }, (_, i) => toneOf({ handle: `cheese-${(0x1000 + i * 7919).toString(16)}` }))
    )
    expect(tones.size, '一个项目里的队友全是一个颜色，就分不出谁是谁').toBeGreaterThan(1)
  })
})

// ---- 眼睛看得清 -------------------------------------------------------------
//
// 眼睛是图形不是文字，按 WCAG 对非文字元素的要求，和底色至少 3:1。颜色写在
// style.css 里，浅色一组在前、深色一组在后；这里读的就是浏览器画的那几个值。

const STYLE = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'style.css'), 'utf8')

function tokenValues(name: string): string[] {
  return Array.from(STYLE.matchAll(new RegExp(`${name}:\\s*(#[0-9a-f]{6})`, 'gi')), (m) => m[1].toLowerCase())
}

function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

describe('AI 队友头像的颜色', () => {
  const inks = tokenValues('--agent-ink')

  it('浅色、深色主题各有一组', () => {
    expect(inks).toHaveLength(2)
  })

  for (const [theme, index] of [
    ['浅色', 0],
    ['深色', 1],
  ] as const) {
    it(`${theme}主题下，每一档底色上的眼睛都看得清`, () => {
      const tones = Array.from({ length: AGENT_TONES }, (_, n) => tokenValues(`--agent-tone-${n}`)[index])
      for (const [n, tone] of tones.entries()) {
        expect(tone, `--agent-tone-${n} 在${theme}主题下没有定义`).toBeTruthy()
        expect(contrast(inks[index], tone), `--agent-tone-${n}（${tone}）上的眼睛太淡`).toBeGreaterThanOrEqual(3)
      }
    })
  }
})
