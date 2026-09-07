// 分支保护 (#718) 设置区的结构断言。和 githubSettingsSections.spec.ts 一个路子：
// 断言的对象是「哪个规则在哪个位置、跟着哪个状态灰掉」——这是模板里的事实，
// 源码扫描直接读它；mount 这个视图要拖上 Vuetify 和十几个 API 调用，反而绕远。
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

const SRC = dirname(fileURLToPath(import.meta.url))
const view = readFileSync(join(SRC, 'views/ProjectSettingsView.vue'), 'utf8')

/** The markup between a `<span class="page-section-title">TITLE</span>` and the
 *  next section title — i.e. one settings section's body. */
function section(title: string): string {
  const head = `<span class="page-section-title">${title}</span>`
  const start = view.indexOf(head)
  expect(start, `section 「${title}」 not found`).toBeGreaterThan(-1)
  const next = view.indexOf('<span class="page-section-title">', start + head.length)
  return view.slice(start, next === -1 ? undefined : next)
}

describe('the 分支保护 section', () => {
  it('照 GitHub 分支保护那一页的顺序列出规则', () => {
    const s = section('分支保护')
    const order = [
      '合并前必须通过的检查',
      '合并前分支必须跟上 main',
      '新提交作废已有的采纳',
      '允许自动合并',
      '人工放行的人',
      '需要几个人批准',
      '合并方式',
      '任务默认 reviewer',
    ]
    let last = -1
    for (const label of order) {
      const at = s.indexOf(label)
      expect(at, `「${label}」 out of order or missing`).toBeGreaterThan(last)
      last = at
    }
  })

  it('GitHub 已开保护时给顶行提示，同名规则灰掉而不是藏起来', () => {
    const s = section('分支保护')
    expect(s).toContain('GitHub 已在执行以下规则')
    expect(s).toContain('bp.github_protection.enforced')
    // 同名规则 = GitHub 那一页也有的五处：必须通过的检查（输入 + 删除）、跟上
    // main、作废采纳、放行名单、批准人数 —— 每一处的 disabled 都绑着 ghEnforced。
    const disabledBindings = s.match(/:disabled="ghEnforced/g) ?? []
    expect(disabledBindings.length).toBeGreaterThanOrEqual(6)
    // 灰掉不是藏起来：没有任何规则行整个躲在 enforced 的 v-if/v-show 后面。
    expect(s).not.toMatch(/v-(?:if|show)="!?ghEnforced/)
  })

  it('查不到 GitHub 状态时不灰，只加一行淡色说明', () => {
    const s = section('分支保护')
    expect(s).toMatch(/status === 'unknown'/)
    expect(s).toContain('暂时查不到 GitHub 侧的保护状态')
  })

  it('合并方式只显示，永远不出现在写回的 patch 里', () => {
    const s = section('分支保护')
    expect(s).toContain('bp.merge_method')
    expect(s).not.toMatch(/merge_method\s*:/)
  })

  it('平台独有的两项（自动合并、任务默认 reviewer）不随 GitHub 灰掉', () => {
    const s = section('分支保护')
    const row = (label: string) => {
      const at = s.indexOf(label)
      expect(at, `「${label}」 not found`).toBeGreaterThan(-1)
      return s.slice(at, s.indexOf('</div>\n\n', at))
    }
    expect(row('允许自动合并')).not.toContain('ghEnforced')
    expect(row('任务默认 reviewer')).not.toContain('ghEnforced')
  })
})
