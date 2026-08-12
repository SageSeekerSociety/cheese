// 现场 读起来是一份聊天记录，所以它得像聊天记录一样：最新的在下面、打开就看得见，
// 而且**一条巨长的输出不能把它前后的东西全挤出屏幕**。
//
// 改之前实机量到的：打开 现场，`scrollTop = 0`，而 `scrollHeight = 1818`、
// 视口 `clientHeight = 500` —— 读者落在离他要看的那一条 1300px 以上的地方。
import { render } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import { countLines, isLongSiteEntry, shouldFollowTail, SITE_CLAMP_LINES } from './siteLog'

describe('isLongSiteEntry', () => {
  it('catches wide prose and tall output alike', () => {
    // 两种「长」都要认。芝士两种都会产：几千字不换行的散文，和一屏一屏的短行
    // 命令输出。只认其中一种，另一种就照样漏过去。
    expect(isLongSiteEntry('x'.repeat(2000))).toBe(true)
    expect(isLongSiteEntry(Array.from({ length: 40 }, () => 'ok').join('\n'))).toBe(true)
  })

  it('leaves an ordinary entry alone', () => {
    expect(isLongSiteEntry('读了一下 runtime.py，改动在超时那一段。')).toBe(false)
    expect(isLongSiteEntry('')).toBe(false)
    expect(isLongSiteEntry(Array.from({ length: SITE_CLAMP_LINES }, () => 'a').join('\n'))).toBe(false)
  })

  it('turns long exactly one line past the clamp', () => {
    // 边界必须和 clamp 对齐：折叠到 12 行却对第 12 行的条目不给展开按钮，
    // 或者给了按钮却没折叠，都是把读者卡在中间。
    const atClamp = Array.from({ length: SITE_CLAMP_LINES }, () => 'a').join('\n')
    const overClamp = Array.from({ length: SITE_CLAMP_LINES + 1 }, () => 'a').join('\n')
    expect(isLongSiteEntry(atClamp)).toBe(false)
    expect(isLongSiteEntry(overClamp)).toBe(true)
  })
})

describe('countLines', () => {
  it('counts what the 展开全部（N 行）label promises', () => {
    expect(countLines('a\nb\nc')).toBe(3)
    expect(countLines('single')).toBe(1)
    expect(countLines('')).toBe(0)
  })
})

describe('shouldFollowTail', () => {
  it('follows when the reader is parked at the bottom', () => {
    expect(shouldFollowTail({ scrollTop: 1318, scrollHeight: 1818, clientHeight: 500 })).toBe(true)
  })

  it('does not yank a reader who scrolled up', () => {
    // 有人往上翻是因为他在读那一条。芝士又落一行工具日志就把他拽回底部，
    // 比让他自己滚回去更糟。
    expect(shouldFollowTail({ scrollTop: 0, scrollHeight: 1818, clientHeight: 500 })).toBe(false)
  })
})

describe('the raw entry renders without the template’s own indentation', () => {
  it('keeps pre-wrap content byte-exact', () => {
    // `.site-msg__raw` 是 `white-space: pre-wrap`，所以模板里那圈缩进如果进了
    // 文本节点，每一条现场记录都会多一个开头换行和一串空格。Vue 默认的
    // `whitespace: 'condense'` 会把「只含空白且带换行」的文本节点丢掉 —— 这条
    // 测试是为了让 prettier 把 {{ b.content }} 换行摆开之后，那个前提仍然成立。
    const content = 'line one\n  indented two'
    const { container } = render(
      {
        props: { content: { type: String, required: true } },
        template: `
          <div class="site-msg__raw">
            {{ content }}
          </div>
        `,
      },
      { props: { content } }
    )
    expect(container.querySelector('.site-msg__raw')?.textContent).toBe(content)
  })
})
