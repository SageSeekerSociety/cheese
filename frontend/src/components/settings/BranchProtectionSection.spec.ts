/** 分支保护那两个下拉画的是「谁」：选中的人/在名单里的人一律走一处 `IdentityView`
 *  画头像 + 姓名，只有「未指定」这种不是人的选项才退回纯文字。这一件是纯 props 的，
 *  能直接挂起来看它真画了什么（同目录的 `src/branchProtectionSection.spec.ts` 管的是
 *  规则顺序那类结构断言，另走源码扫描）。 */
import type { BranchProtection } from '@/branchProtection'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import BranchProtectionSection from './BranchProtectionSection.vue'

import { setLocale } from '@/i18n'

beforeEach(() => setLocale('en'))
afterEach(cleanup)

const bp = (over: Partial<BranchProtection> = {}): BranchProtection => ({
  required_checks: [],
  strict: false,
  dismiss_stale: false,
  auto_merge_allowed: true,
  override_handles: null,
  approvals_required: 1,
  default_reviewer: '',
  dispatch_seat: '',
  merge_method: 'squash',
  github_protection: { enforced: false, status: 'none' },
  ...over,
})

function mount(over: Partial<BranchProtection> = {}) {
  return render(BranchProtectionSection, {
    props: {
      state: 'loaded' as const,
      loadError: null,
      bp: bp(over),
      saving: null,
      error: null,
      memberItems: [{ title: 'Alice', value: 'alice', handle: 'alice', name: 'Alice', avatarId: 7, agent: false }],
      reviewerItems: [
        { title: 'Unassigned', value: '', handle: '', name: '', avatarId: null, agent: null },
        { title: 'Alice', value: 'alice', handle: 'alice', name: 'Alice', avatarId: 7, agent: false },
      ],
      dispatchSeatItems: [],
      checkName: '',
      checkPaths: '',
      approvalsDraft: '1',
    },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

describe('分支保护下拉里的身份', () => {
  it('选中的审阅人画头像 + 姓名，不是一行纯文字', async () => {
    const { container } = await mount({ default_reviewer: 'alice' })
    const selection = container.querySelector('.v-select__selection')
    expect(selection?.querySelector('.identity-view')).not.toBeNull()
    expect(selection?.textContent).toContain('Alice')
  })

  it('「未指定」不是人：画纯文字，不给它一张脸', async () => {
    const { container } = await mount({ default_reviewer: '' })
    const selection = container.querySelector('.v-select__selection')
    expect(selection?.querySelector('.identity-view')).toBeNull()
    expect(selection?.textContent).toContain('Unassigned')
  })

  it('放行名单里选中的每一颗 chip 也是头像 + 姓名', async () => {
    const { container } = await mount({ override_handles: ['alice'] })
    const chip = container.querySelector('.v-chip')
    expect(chip?.querySelector('.identity-view')).not.toBeNull()
    expect(chip?.textContent).toContain('Alice')
  })
})
