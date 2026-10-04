/** 分支保护的布尔开关是乐观的：点下去先翻到人点的那一面，PUT 回来用服务器的收口，
 *  失败整份退回。 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api', async () => ({
  ...(await vi.importActual<typeof import('@/api')>('@/api')),
  getBranchProtection: vi.fn(),
  listProjectMembers: vi.fn().mockResolvedValue({ data: [] }),
  setBranchProtection: vi.fn(),
}))

import type { BranchProtection } from '@/cx_types'

import { useBranchProtection } from './useBranchProtection'

import { getBranchProtection, setBranchProtection } from '@/api'

const rules = (over: Partial<BranchProtection> = {}): BranchProtection => ({
  required_checks: [],
  strict: false,
  dismiss_stale: false,
  auto_merge_allowed: false,
  override_handles: null,
  approvals_required: 1,
  default_reviewer: '',
  merge_method: 'squash',
  github_protection: { enforced: false, status: 'none' },
  ...over,
})

beforeEach(() => {
  vi.clearAllMocks()
})

describe('分支保护的开关', () => {
  it('PUT 还没回来，开关已经翻过去了；回来后保持服务器那一份', async () => {
    vi.mocked(getBranchProtection).mockResolvedValue(rules())
    const bp = useBranchProtection(() => 'p')
    await bp.loadBranchProtection()

    let resolveApi: (b: BranchProtection) => void = () => {}
    vi.mocked(setBranchProtection).mockReturnValue(new Promise<BranchProtection>((res) => (resolveApi = res)))
    const saving = bp.saveBranchProtection({ strict: true }, 'strict')

    // 乐观：还没等 PUT。
    expect(bp.bp.value?.strict).toBe(true)

    resolveApi(rules({ strict: true }))
    await saving
    expect(bp.bp.value?.strict).toBe(true)
  })

  it('PUT 失败：整份退回改动前', async () => {
    vi.mocked(getBranchProtection).mockResolvedValue(rules({ strict: true }))
    const bp = useBranchProtection(() => 'p')
    await bp.loadBranchProtection()

    vi.mocked(setBranchProtection).mockRejectedValue(new Error('boom'))
    const ok = await bp.saveBranchProtection({ strict: false }, 'strict')

    expect(ok).toBe(false)
    expect(bp.bp.value?.strict).toBe(true)
    expect(bp.bpError.value).toBeTruthy()
  })

  it('多个控件各改各的：乐观值不相干的那一项不受影响', async () => {
    vi.mocked(getBranchProtection).mockResolvedValue(rules())
    const bp = useBranchProtection(() => 'p')
    await bp.loadBranchProtection()

    vi.mocked(setBranchProtection).mockRejectedValue(new Error('boom'))
    await bp.saveBranchProtection({ dismiss_stale: true }, 'dismiss_stale')

    expect(bp.bp.value?.dismiss_stale).toBe(false)
    expect(bp.bp.value?.strict).toBe(false)
  })
})
