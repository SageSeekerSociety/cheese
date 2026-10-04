/// <reference types="node" />
/**
 * 一份「离屏跳过」（content-visibility: auto）的内容必须同时满足三件事，缺一不可：
 *
 *   1. `contain-intrinsic-size` 带 `auto` —— 渲染过的那一份记住真实高度，只对**从未
 *      渲染过**的用那个估计值占位。少了 `auto`，量到的一直是估计值，翻页会跳、落点
 *      会偏。
 *   2. 有一个 `.cv-measure <选择器>` 的后门 —— 要按真实高度量的地方（向上翻页补偿、
 *      scrollIntoView，见 lib/contentVisibility）能把这一份关掉，让整窗按真实高度
 *      铺开再量。
 *   3. 内容留在 DOM 里（这是「不虚拟化」）—— 靠的是只写 content-visibility，不写
 *      display / visibility。
 *
 * jsdom 量不到布局、也算不出 content-visibility 的级联，照仓库的老办法用源码断言
 * 钉住（见 vShowDisplayUtilities.spec.ts、views/feedback/scroll.spec.ts）。
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

const SRC = dirname(fileURLToPath(import.meta.url))

function read(rel: string): string {
  return readFileSync(join(SRC, rel), 'utf8')
}

function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/** 某个选择器（能逗号并列）那条规则的规则体。 */
function ruleBody(css: string, selector: string): string {
  const m = new RegExp(`${escapeRe(selector)}\\s*\\{([^}]*)\\}`).exec(css)
  return m ? m[1] : ''
}

/** 以 `.cv-measure` 起头、包含这个选择器的规则（可能是逗号并列里的一个）的规则体。 */
function measureOffBody(css: string, selector: string): string {
  const m = new RegExp(`\\.cv-measure\\s+${escapeRe(selector)}[^{]*\\{([^}]*)\\}`).exec(css)
  return m ? m[1] : ''
}

// 今天靠离屏跳过省渲染的重复块。时间线那两条在 #2654 之后补的，文档视图那两条是本轮。
const SURFACES = [
  { file: 'components/AgentNoticeFrame.vue', selector: '.notice-row' },
  { file: 'components/room/RoomNotice.vue', selector: '.room-happening' },
  { file: 'views/ProjectDocsView.vue', selector: '.memory-card' },
  { file: 'views/ProjectDocsView.vue', selector: '.weekly-card' },
]

describe('离屏跳过的块都带 auto 估计和测量帧后门', () => {
  for (const { file, selector } of SURFACES) {
    it(`${file} 的 ${selector}`, () => {
      const css = read(file)
      const body = ruleBody(css, selector)
      // 跳过离屏内容，但留在 DOM 里。
      expect(body).toContain('content-visibility: auto')
      // 估计高度必须带 auto：渲染过的记住真实高度。
      expect(body).toMatch(/contain-intrinsic-size:\s*auto\s+\d+px/)
      // 测量帧里关掉，好让整窗按真实高度铺开再量。
      expect(measureOffBody(css, selector)).toContain('content-visibility: visible')
    })
  }
})
