// 连接 GitHub 账号 / 连接 GitHub 仓库 are two independent flows with two
// buttons, and each one's outcome comes back as a query param on the SAME
// page. They shared one notice ref, and that ref was only rendered inside the
// 仓库 section — so finishing the ACCOUNT flow showed 「已连接 GitHub 账号。」
// under the 「连接 GitHub 仓库」 heading. Nothing failed; it just announced the
// result of one action above a different one.
//
// A source scan rather than a mount: the defect is *which section the element
// sits in*, which is a fact about the template, and mounting this view drags in
// Vuetify plus a dozen API calls to assert something the markup states directly.
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

const SRC = dirname(fileURLToPath(import.meta.url))
const view = readFileSync(join(SRC, 'views/ProjectSettingsView.vue'), 'utf8')

/** One settings section: from its `data-section` title to the next section
 *  title. Anchored by attribute rather than by heading text — the headings are
 *  catalog entries now, so their wording lives in the message files and the
 *  template no longer spells it out. */
function section(name: string): string {
  const head = `<span class="page-section-title" data-section="${name}">`
  const start = view.indexOf(head)
  expect(start, `section 「${name}」 not found`).toBeGreaterThan(-1)
  const next = view.indexOf('<span class="page-section-title"', start + head.length)
  return view.slice(start, next === -1 ? undefined : next)
}

describe('the GitHub settings sections', () => {
  it('shows the 账号 flow outcome in the 账号 section', () => {
    const account = section('account')
    expect(account).toContain('githubAccountNotice')
    expect(account).not.toContain('githubRepoNotice')
  })

  it('shows the 仓库 flow outcome in the 仓库 section', () => {
    const repo = section('repo')
    expect(repo).toContain('githubRepoNotice')
    expect(repo).not.toContain('githubAccountNotice')
  })

  it('has no shared notice ref left to regress into', () => {
    expect(view).not.toContain('githubCallbackNotice')
  })

  it('never splices a raw reason code into user-facing text', () => {
    // `连接账号失败：already_linked` is what a real user saw (#222). The reason
    // code must reach a lookup table, never a template literal.
    expect(view).not.toMatch(/失败：\$\{[^}]*reason/)
  })
})
