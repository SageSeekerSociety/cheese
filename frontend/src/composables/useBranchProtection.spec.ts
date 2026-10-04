// 分支保护的设置开关是乐观的：一翻就过去，服务端那份回来覆盖，失败弹回原值。
import type { BranchProtection, BranchProtectionRules } from '@/cx_types'

import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api', async () => ({
  ...(await vi.importActual<typeof import('@/api')>('@/api')),
  getBranchProtection: vi.fn(),
  setBranchProtection: vi.fn(),
  listProjectMembers: vi.fn().mockResolvedValue({ data: [] }),
}))

import { useBranchProtection } from './useBranchProtection'

import { getBranchProtection, setBranchProtection } from '@/api'

const rules = (over: Partial<BranchProtectionRules> = {}): BranchProtection => ({
  required_checks: [],
  strict: false,
  dismiss_stale: false,
  auto_merge_allowed: true,
  override_handles: null,
  approvals_required: 1,
  default_reviewer: '',
  merge_method: 'squash',
  github_protection: { enforced: false, status: 'none' },
  ...over,
})

beforeEach(() => vi.clearAllMocks())

async function loaded() {
  vi.mocked(getBranchProtection).mockResolvedValue(rules())
  const bp = useBranchProtection(() => 'p')
  await bp.loadBranchProtection()
  return bp
}

describe('分支保护开关的乐观更新', () => {
  it('开关当场翻过去，不等服务端；成功用服务端那份覆盖', async () => {
    const bp = await loaded()
    let release!: (r: BranchProtectionRules) => void
    vi.mocked(setBranchProtection).mockReturnValue(new Promise((res) => (release = res)))

    const done = bp.saveBranchProtection({ strict: true }, 'strict')
    expect(bp.bp.value?.strict).toBe(true)

    release(rules({ strict: true }))
    expect(await done).toBe(true)
    expect(bp.bp.value?.strict).toBe(true)
    expect(bp.bpError.value).toBeNull()
  })

  it('失败：弹回原值，并报错', async () => {
    const bp = await loaded()
    vi.mocked(setBranchProtection).mockRejectedValue(new Error('boom'))

    const done = bp.saveBranchProtection({ strict: true }, 'strict')
    expect(bp.bp.value?.strict).toBe(true)

    expect(await done).toBe(false)
    expect(bp.bp.value?.strict).toBe(false)
    expect(bp.bpError.value).toBe('boom')
  })

  it('只改这一条：乐观与回滚都不碰别的规则', async () => {
    const bp = await loaded()
    vi.mocked(setBranchProtection).mockRejectedValue(new Error('boom'))
    await bp.saveBranchProtection({ auto_merge_allowed: false }, 'auto_merge_allowed')
    expect(bp.bp.value?.auto_merge_allowed).toBe(true)
    expect(bp.bp.value?.dismiss_stale).toBe(false)
  })
})
