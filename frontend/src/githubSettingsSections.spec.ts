/// <reference types="node" />
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
const settings = (name: string) => readFileSync(join(SRC, `components/settings/${name}.vue`), 'utf8')

/** The markup between a `<span class="page-section-title">TITLE</span>` and the
 *  end of its `<section>` — i.e. one settings section's body. Since the split
 *  (#2143) each section is its own component, so scan the component files. */
function section(key: string): string {
  for (const file of [settings('GithubAccountSettings'), settings('GithubRepoSettings')]) {
    // 标题从词条库取：`githubAccount.title` 是「连接 GitHub 账号」，`githubRepo.title` 是「连接 GitHub 仓库」。
    const head = `<span class="page-section-title">{{ t('work.projectSettings.${key}.title') }}</span>`
    const start = file.indexOf(head)
    if (start === -1) continue
    const end = file.indexOf('</section>', start)
    return file.slice(start, end === -1 ? undefined : end)
  }
  throw new Error(`section 「${key}」 not found`)
}

/** The page's binding block for one component tag: `<Tag` through its `/>`.
 *  Each notice reaches its section as a prop now, so which flow's outcome lands
 *  where is a fact about these bindings. The runtime half of this — that a
 *  `?github_install=success` callback really renders under 「连接 GitHub 仓库」
 *  and not under 「连接 GitHub 账号」 — is pinned by
 *  `views/ProjectSettingsView.sections.spec.ts` against the real DOM. */
function binding(tag: string): string {
  const at = view.indexOf(`<${tag}`)
  expect(at, `page does not render <${tag}>`).toBeGreaterThan(-1)
  return view.slice(at, view.indexOf('/>', at))
}

describe('the GitHub settings sections', () => {
  it('shows the 账号 flow outcome in the 账号 section', () => {
    const account = binding('GithubAccountSettings')
    expect(account).toContain('githubAccountNotice')
    expect(account).not.toContain('githubRepoNotice')
    expect(section('githubAccount')).toContain('notice')
  })

  it('shows the 仓库 flow outcome in the 仓库 section', () => {
    const repo = binding('GithubRepoSettings')
    expect(repo).toContain('githubRepoNotice')
    expect(repo).not.toContain('githubAccountNotice')
    expect(section('githubRepo')).toContain('notice')
  })

  it('has no shared notice ref left to regress into', () => {
    expect(view).not.toContain('githubCallbackNotice')
    expect(view + settings('GithubRepoSettings') + settings('GithubAccountSettings')).not.toContain(
      'githubCallbackNotice'
    )
  })

  it('never splices a raw reason code into user-facing text', () => {
    // `连接账号失败：already_linked` is what a real user saw (#222). The reason
    // code must reach a lookup table, never a template literal.
    for (const file of [view, settings('GithubAccountSettings'), settings('GithubRepoSettings')]) {
      expect(file).not.toMatch(/失败：\$\{[^}]*reason/)
    }
  })
})
