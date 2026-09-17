// 分支保护 (#718) 设置区的结构断言。和 githubSettingsSections.spec.ts 一个路子：
// 断言的对象是「哪个规则在哪个位置、跟着哪个状态灰掉」——这是模板里的事实，
// 源码扫描直接读它；mount 这个视图要拖上 Vuetify 和十几个 API 调用，反而绕远。
//
// 文案进了词表之后，这里锚的是**键**而不是渲染出来的字：规则叫什么由
// `projects.branchProtection.*` 决定，顺序仍然是模板里的事实。
// 区块本身用 `data-section` 锚——它不受翻译和排版影响。
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

const SRC = dirname(fileURLToPath(import.meta.url))
const view = readFileSync(join(SRC, 'views/ProjectSettingsView.vue'), 'utf8')

/** One settings section: from its `data-section` title to the next section title. */
function section(name: string): string {
  const head = `<span class="page-section-title" data-section="${name}">`
  const start = view.indexOf(head)
  expect(start, `section 「${name}」 not found`).toBeGreaterThan(-1)
  const next = view.indexOf('<span class="page-section-title"', start + head.length)
  return view.slice(start, next === -1 ? undefined : next)
}

/** 规则文案在源码里的样子——`t('projects.branchProtection.strict')`。 */
const key = (name: string) => `t('projects.branchProtection.${name}')`

describe('the 分支保护 section', () => {
  it('照 GitHub 分支保护那一页的顺序列出规则', () => {
    const s = section('branchProtection')
    const order = [
      'requiredChecks',
      'strict',
      'dismissStale',
      'autoMerge',
      'overrideHandles',
      'approvals',
      'mergeMethod',
      'defaultReviewer',
    ]
    let last = -1
    for (const name of order) {
      const at = s.indexOf(key(name))
      expect(at, `「${name}」 out of order or missing`).toBeGreaterThan(last)
      last = at
    }
  })

  it('GitHub 已开保护时给顶行提示，同名规则灰掉而不是藏起来', () => {
    const s = section('branchProtection')
    expect(s).toContain(key('enforcedByGithub'))
    expect(s).toContain('bp.github_protection.enforced')
    // 同名规则 = GitHub 那一页也有的五处：必须通过的检查（输入 + 删除）、跟上
    // main、作废采纳、放行名单、批准人数 —— 每一处的 disabled 都绑着 ghEnforced。
    const disabledBindings = s.match(/:disabled="ghEnforced/g) ?? []
    expect(disabledBindings.length).toBeGreaterThanOrEqual(6)
    // 灰掉不是藏起来：没有任何规则行整个躲在 enforced 的 v-if/v-show 后面。
    expect(s).not.toMatch(/v-(?:if|show)="!?ghEnforced/)
  })

  it('查不到 GitHub 状态时不灰，只加一行淡色说明', () => {
    const s = section('branchProtection')
    expect(s).toMatch(/status === 'unknown'/)
    expect(s).toContain(key('statusUnknown'))
  })

  it('合并方式只显示，永远不出现在写回的 patch 里', () => {
    const s = section('branchProtection')
    expect(s).toContain('bp.merge_method')
    expect(s).not.toMatch(/merge_method\s*:/)
  })

  it('平台独有的两项（自动合并、任务默认 reviewer）不随 GitHub 灰掉', () => {
    const s = section('branchProtection')
    const row = (name: string) => {
      const at = s.indexOf(key(name))
      expect(at, `「${name}」 not found`).toBeGreaterThan(-1)
      const next = s.indexOf('class="bp-row', at)
      return s.slice(at, next === -1 ? undefined : next)
    }
    expect(row('autoMerge')).not.toContain('ghEnforced')
    expect(row('defaultReviewer')).not.toContain('ghEnforced')
  })
})
