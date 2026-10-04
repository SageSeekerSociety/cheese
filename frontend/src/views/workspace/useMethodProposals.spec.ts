// 房间里的工作方法提议：只请人决定这个房间里芝士提的、还在等人的那些；保存和拒绝各走
// 各的，没成就原样留着让人再点。
import type { ProjectSkill } from '@/lib/projectSkill'

import { ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useMethodProposals } from './useMethodProposals'

vi.mock('@/api/projectSkills', () => ({
  listProjectSkills: vi.fn(),
  confirmProjectSkill: vi.fn(),
  declineProjectSkill: vi.fn(),
}))

const { listProjectSkills, confirmProjectSkill, declineProjectSkill } = await import('@/api/projectSkills')

function skill(over: Partial<ProjectSkill>): ProjectSkill {
  return {
    id: 'm',
    project_id: 'p1',
    name: 'weekly-report',
    title: '周报',
    description: '用户要周报时',
    inputs: '',
    steps: '先写变坏的指标',
    outputs: '',
    files: {},
    state: 'draft',
    shipped_revision: 0,
    proposed_by: 'cheese-x',
    confirmed_by: null,
    confirmed_at: null,
    source_topic_id: 'room-1',
    proposal: { taught: ['先说坏消息'], accepted: '用户说就这样' },
    created_at: '2026-10-04T00:00:00Z',
    updated_at: '2026-10-04T00:00:00Z',
    ...over,
  }
}

const mine = skill({ id: 'mine' })

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(listProjectSkills).mockResolvedValue({
    data: [
      mine,
      skill({ id: 'other-room', source_topic_id: 'room-2' }),
      skill({ id: 'written-by-a-person', proposal: null }),
      skill({ id: 'saved', state: 'active', shipped_revision: 1, proposal: null }),
    ],
    total: 4,
  })
})

async function loaded() {
  const methods = useMethodProposals(ref('p1'), ref('room-1'))
  await methods.load()
  return methods
}

describe('useMethodProposals', () => {
  it('asks only about the teammate proposals waiting in this room', async () => {
    const methods = await loaded()
    expect(methods.proposals.value.map((s) => s.id)).toEqual(['mine'])
  })

  it('saves a proposal and does not decline it', async () => {
    vi.mocked(confirmProjectSkill).mockResolvedValue({ ...mine, state: 'active', shipped_revision: 1 })
    const methods = await loaded()
    await methods.save(mine)
    expect(confirmProjectSkill).toHaveBeenCalledWith('mine')
    expect(declineProjectSkill).not.toHaveBeenCalled()
    expect(methods.proposals.value).toEqual([])
    expect(methods.saved.value.map((s) => s.id)).toEqual(['mine'])
  })

  it('declines a proposal without saving it', async () => {
    vi.mocked(declineProjectSkill).mockResolvedValue({ ...mine, state: 'draft' })
    const methods = await loaded()
    await methods.decline(mine)
    expect(declineProjectSkill).toHaveBeenCalledWith('mine')
    expect(confirmProjectSkill).not.toHaveBeenCalled()
    expect(methods.proposals.value).toEqual([])
    expect(methods.saved.value).toEqual([])
  })

  it('keeps the proposal and says why when saving fails', async () => {
    vi.mocked(confirmProjectSkill).mockRejectedValue(new Error('网络断了'))
    const methods = await loaded()
    await methods.save(mine)
    expect(methods.proposals.value.map((s) => s.id)).toEqual(['mine'])
    expect(methods.saved.value).toEqual([])
    expect(methods.error.value).toBe('网络断了')
  })
})
