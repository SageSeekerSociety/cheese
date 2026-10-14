// 分支保护的设置开关是乐观的：一翻就过去，服务端那份回来覆盖，失败弹回原值。
import type { BranchProtection, BranchProtectionRules } from '@/branchProtection'
import type { ProjectAgent } from '@/cx_types'

import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api', async () => ({
  ...(await vi.importActual<typeof import('@/api')>('@/api')),
  getBranchProtection: vi.fn(),
  setBranchProtection: vi.fn(),
  listProjectMembers: vi.fn().mockResolvedValue({ data: [] }),
  listProjectAgents: vi.fn().mockResolvedValue({ data: [] }),
}))

import { useBranchProtection } from './useBranchProtection'

import { getBranchProtection, listProjectAgents, listProjectMembers, setBranchProtection } from '@/api'
import { t } from '@/i18n'

const rules = (over: Partial<BranchProtectionRules> = {}): BranchProtection => ({
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

describe('分支保护下拉的可选项', () => {
  it('放行名单每一项带上身份字段，供一处 IdentityView 画头像 + 姓名', async () => {
    vi.mocked(getBranchProtection).mockResolvedValue(rules())
    vi.mocked(listProjectMembers).mockResolvedValue({
      data: [
        { user_handle: 'alice', name: 'Alice', avatar_id: 7, agent: false },
        // AI 队友不进放行名单，选项里不该出现它。
        { user_handle: 'cheese-abc', name: 'AI', avatar_id: null, agent: true },
      ],
    } as never)
    const bp = useBranchProtection(() => 'p')
    await bp.loadBranchProtection()
    expect(bp.bpMemberItems.value).toEqual([
      { title: 'Alice', value: 'alice', handle: 'alice', name: 'Alice', avatarId: 7, agent: false },
    ])
    // 「未指定」不是某个人：handle 空着，模板据此画纯文字而不是一张脸。
    expect(bp.bpReviewerItems.value[0]).toEqual({
      title: t('work.projectSettings.merge.unassigned'),
      value: '',
      handle: '',
      name: '',
      avatarId: null,
      agent: null,
    })
    expect(bp.bpReviewerItems.value[1]).toEqual(bp.bpMemberItems.value[0])
  })
})

// 调度座位那一栏填的是 AI 队友的席位，选的是「以后的活交给谁」；停用的队友
// （`is_active: false`）不该出现，它已经待着的房间不受影响。
describe('调度座位的候选人名单', () => {
  const agent = (over: Partial<ProjectAgent> = {}): ProjectAgent => ({
    configuration: { body: '', skills: [] },
    id: 'a1',
    project_id: 'p',
    handle: 'h1',
    seat_handle: 'cheese-live',
    type_name: null,
    display_name: '在用的',
    is_default: false,
    is_active: true,
    ...over,
  })

  it('停用的不出现在名单里，在用的照旧出现', async () => {
    vi.mocked(listProjectAgents).mockResolvedValue({
      data: [agent(), agent({ id: 'a2', seat_handle: 'cheese-off', display_name: '停用的', is_active: false })],
      total: 2,
    })

    const bp = await loaded()
    // 第一项是「不指派」（空值），后面才是队友。
    expect(bp.bpDispatchItems.value.map((i) => i.value)).toEqual(['', 'cheese-live'])
    expect(bp.bpDispatchItems.value.map((i) => i.title)).not.toContain('停用的')
  })
})
