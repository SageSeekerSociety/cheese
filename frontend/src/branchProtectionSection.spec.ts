/// <reference types="node" />
// 分支保护 (#718) 设置区的结构断言。和 githubSettingsSections.spec.ts 一个路子：
// 断言的对象是「哪个规则在哪个位置、跟着哪个状态灰掉」——这是模板里的事实，
// 源码扫描直接读它；mount 这个视图要拖上 Vuetify 和十几个 API 调用，反而绕远。
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

const SRC = dirname(fileURLToPath(import.meta.url))
// 拆分（#2143）之后这一块自己是一个组件，扫的对象跟着搬过来。断言的东西没变：
// 还是「哪条规则在什么位置、跟着哪个状态灰掉」。
const view = readFileSync(join(SRC, 'components/settings/BranchProtectionSection.vue'), 'utf8')

/** 这一块里的文字都从词条库取：按键名找，`label('checks')` 就是
 *  `work.projectSettings.merge.checks` 那一句（「合并前必须通过的检查」）。 */
const label = (key: string) => `work.projectSettings.merge.${key}`

/** The markup between the section title and the end of its `<section>` — i.e.
 *  one settings section's body. */
function section(key: string): string {
  const head = `<span class="page-section-title">{{ t('${label(key)}') }}</span>`
  const start = view.indexOf(head)
  expect(start, `section 「${key}」 not found`).toBeGreaterThan(-1)
  const end = view.indexOf('</section>', start)
  return view.slice(start, end === -1 ? undefined : end)
}

describe('the 分支保护 section', () => {
  it('照 GitHub 分支保护那一页的顺序列出规则', () => {
    const s = section('title')
    // 必须通过的检查、跟上 main、作废采纳、自动合并、放行名单、批准人数、合并方式、默认审阅
    const order = [
      'checks',
      'strict',
      'dismissStale',
      'autoMerge',
      'override',
      'approvals',
      'mergeMethod',
      'defaultReviewer',
    ]
    let last = -1
    for (const key of order) {
      const at = s.indexOf(`'${label(key)}'`)
      expect(at, `「${key}」 out of order or missing`).toBeGreaterThan(last)
      last = at
    }
  })

  it('GitHub 已开保护时给顶行提示，同名规则灰掉而不是藏起来', () => {
    const s = section('title')
    expect(s).toContain(label('githubEnforced'))
    expect(s).toContain('bp.github_protection.enforced')
    // 同名规则 = GitHub 那一页也有的五处：必须通过的检查（输入 + 删除）、跟上
    // main、作废采纳、放行名单、批准人数 —— 每一处的 disabled 都绑着 ghEnforced。
    const disabledBindings = s.match(/:disabled="ghEnforced/g) ?? []
    expect(disabledBindings.length).toBeGreaterThanOrEqual(6)
    // 灰掉不是藏起来：没有任何规则行整个躲在 enforced 的 v-if/v-show 后面。
    expect(s).not.toMatch(/v-(?:if|show)="!?ghEnforced/)
  })

  it('查不到 GitHub 状态时不灰，只加一行淡色说明', () => {
    const s = section('title')
    expect(s).toMatch(/status === 'unknown'/)
    expect(s).toContain(label('githubUnknown'))
  })

  it('合并方式只显示，永远不出现在写回的 patch 里', () => {
    const s = section('title')
    expect(s).toContain('bp.merge_method')
    expect(s).not.toMatch(/merge_method\s*:/)
  })

  it('平台独有的两项（自动合并、默认审阅）不随 GitHub 灰掉', () => {
    const s = section('title')
    const row = (key: string) => {
      const at = s.indexOf(`'${label(key)}'`)
      expect(at, `「${key}」 not found`).toBeGreaterThan(-1)
      return s.slice(at, s.indexOf('</div>\n\n', at))
    }
    expect(row('autoMerge')).not.toContain('ghEnforced')
    expect(row('defaultReviewer')).not.toContain('ghEnforced')
  })
})
