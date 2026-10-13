// 动效台的清单：每一格都得真引产品的时长 token，名字还得和 `src/style.css` 对得上。
//
// 这一条防的是「悄悄不生效」：清单里写 `--dur-base`、样式表里改名成别的，页面照样
// 渲染（代码片上就是一行字），只有这里会红。
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

import { DURATION_TOKENS } from './demoMotion'
import { TRANSITIONS } from './demoTransitions'

// 从磁盘读：测试配置把样式表 import 挡掉了，`?raw` 也一样。
const styleCss = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '../../style.css'), 'utf8')

/** `:root` 那一块里声明了哪些名字。注释里那几个 `--x` 不算：那几行后面跟的不是冒号。 */
function declaredInRoot(): Set<string> {
  const block = /^:root \{([\s\S]*?)^\}/m.exec(styleCss)?.[1] ?? ''
  const names = Array.from(block.matchAll(/^\s*(--[\w-]+)\s*:\s*\S/gm), (hit) => hit[1] ?? '')
  return new Set(names)
}

const DECLARED = declaredInRoot()

describe('动效台的清单', () => {
  it('真读到了 src/style.css 的 :root —— 读不到的话下面几条等于没测', () => {
    expect(DECLARED.has('--radius-md')).toBe(true)
    expect(DECLARED.has('--dur-base')).toBe(true)
    expect(DECLARED.size).toBeGreaterThan(80)
  })

  it('每一格至少引一条时长 token：不写时长的「动效」没法被放慢', () => {
    for (const entry of TRANSITIONS) {
      const durations = entry.tokens.filter((token) => token.startsWith('--dur-'))
      expect(durations, `${entry.id} 一条 --dur-* 都没写`).not.toHaveLength(0)
    }
  })

  it('清单里每一个 token 名字，:root 里都真有', () => {
    for (const entry of TRANSITIONS) {
      for (const token of entry.tokens) {
        expect(DECLARED.has(token), `${entry.id} 写着 ${token}，:root 里没有这个名字`).toBe(true)
      }
    }
  })

  it('慢放要改的那几条也在 :root 里：不然改的是一个不存在的名字', () => {
    for (const token of DURATION_TOKENS) {
      expect(DECLARED.has(token), `${token} 不在 :root 里`).toBe(true)
    }
  })

  it('每一格有唯一的 id、有名字、有说得清的一句话', () => {
    const ids = TRANSITIONS.map((entry) => entry.id)
    expect(new Set(ids).size).toBe(ids.length)
    for (const entry of TRANSITIONS) {
      expect(entry.name.length, entry.id).toBeGreaterThan(0)
      expect(entry.about.length, entry.id).toBeGreaterThan(8)
    }
  })
})
