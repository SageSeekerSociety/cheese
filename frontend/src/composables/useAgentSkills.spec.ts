// 技能面板那点会算错的事：打开时从队友身上抄一份名单、把池子拼出来；加/减一颗之后
// 落库的是**这个队友带的全部名字**（整份覆盖，不是只发这一颗）；落库失败要翻回去；
// 超过上限要拦住；机器只认**我自己**的（在线的优先）。面板画成什么样这里不管。
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/machineSkills', () => ({
  listMachineSkills: vi.fn(),
  fetchMachineSkill: vi.fn(),
  fetchSkillMarket: vi.fn(),
}))

vi.mock('../api/projectSkills', () => ({
  getSkillHealth: vi.fn(),
  listProjectSkills: vi.fn(),
  listSkillCatalog: vi.fn(),
}))

vi.mock('@/api', () => ({
  getProjectDefaultModel: vi.fn(),
  listMyDevices: vi.fn(),
  setAgentEditing: vi.fn(),
  updateProjectAgent: vi.fn(),
}))

import { fetchMachineSkill, fetchSkillMarket, listMachineSkills } from '../api/machineSkills'
import { getSkillHealth, listProjectSkills, listSkillCatalog } from '../api/projectSkills'

import { useAgentSkills } from './useAgentSkills'

import { getProjectDefaultModel, listMyDevices, setAgentEditing, updateProjectAgent } from '@/api'
import { ApiError } from '@/api/http'
import { setLocale, t } from '@/i18n'

const CATALOG = [
  { name: 'wolfram', description: '算' },
  { name: 'documents', description: '写' },
]

function agent(skills: string[]) {
  return {
    id: 'a1',
    handle: 'a1',
    seat_handle: 'a1',
    display_name: '米洛',
    name_source: 'human' as const,
    project_id: 'p1',
    type_name: null,
    is_default: false,
    is_active: true,
    version: 1,
    configuration: { body: '', skills, model: null, effort: null, compact_percent: null },
  }
}

function skills(
  onError: (m: string) => void = () => {},
  onSaved?: (id: string, s: string[], version: number) => void,
  carried = ['wolfram'],
  onStale?: () => void
) {
  return useAgentSkills(
    () => 'p1',
    () => agent(carried),
    onError,
    onSaved,
    onStale
  )
}

beforeEach(() => {
  setLocale('zh-CN')
  vi.mocked(listSkillCatalog)
    .mockReset()
    .mockResolvedValue({ data: CATALOG, total: 2 } as never)
  vi.mocked(listProjectSkills)
    .mockReset()
    .mockResolvedValue({ data: [], total: 0 } as never)
  vi.mocked(listMyDevices)
    .mockReset()
    .mockResolvedValue({
      devices: [
        { device_id: 'dev1', name: '家里', online: false, project_ids: [], team_ids: [], screens: [] },
        { device_id: 'dev2', name: '公司', online: true, project_ids: [], team_ids: [], screens: [] },
      ],
    } as never)
  vi.mocked(getProjectDefaultModel)
    .mockReset()
    .mockResolvedValue({ can_manage: true } as never)
  vi.mocked(listMachineSkills)
    .mockReset()
    .mockResolvedValue({
      skills: [
        {
          name: 'ponytail',
          description: '懒',
          source_root: '/home/me/.claude/skills/ponytail',
          digest: 'd',
          state: 'adopted',
          adopted: true,
        },
        {
          name: 'draft-skill',
          description: '没采纳',
          source_root: '/home/me/.claude/skills/draft-skill',
          digest: 'e',
          state: 'seen',
          adopted: false,
        },
      ],
    } as never)
  vi.mocked(updateProjectAgent)
    .mockReset()
    .mockResolvedValue({ ...agent([]) } as never)
  vi.mocked(setAgentEditing)
    .mockReset()
    .mockResolvedValue({ editing: null } as never)
  vi.mocked(getSkillHealth).mockReset().mockResolvedValue({ status: 'ok', detail: '' })
  vi.mocked(fetchSkillMarket)
    .mockReset()
    .mockResolvedValue({
      skills: [
        {
          name: 'pdf',
          description: '读 PDF',
          url: 'https://github.com/o/r/tree/main/skills/pdf',
          source: 'anthropic',
          kind: 'skill',
        },
      ],
      plugins: [
        {
          name: 'reviewer',
          description: '看代码',
          url: 'https://github.com/o/r/tree/main/plugins/reviewer',
          source: 'anthropic',
          kind: 'plugin',
        },
      ],
      plugin_total: 315,
      mcp: [{ name: 'Acme', description: 'd', url: '', source: 'mcp', kind: 'mcp' }],
      notes: [],
    } as never)
})

describe('队友技能面板', () => {
  it('打开时抄下队友的名单、拼出池子、挑我自己的机器并探一次健康', async () => {
    const s = skills()

    await s.openSkills()

    expect(s.carried.value).toEqual(['wolfram'])
    // 池子：平台两颗 + 机器上扫到的（含还没人「采纳」过的）。勾上才带进会话。
    expect(s.pool.value.map((p) => p.name)).toEqual(['wolfram', 'documents', 'ponytail', 'draft-skill'])
    expect(s.deviceId.value).toBe('dev2')
    expect(s.canManage.value).toBe(true)
    expect(s.skillHealth.value.wolfram?.status).toBe('ok')
    expect(s.loading.value).toBe(false)
  })

  it('加一颗：落库的是写在这个队友身上的整份名单，并把结果回给父组件', async () => {
    const saved = vi.fn()
    const s = skills(() => {}, saved)
    await s.openSkills()

    await s.adoptSkill('documents')

    expect(updateProjectAgent).toHaveBeenCalledWith('p1', 'a1', {
      configuration: expect.objectContaining({ skills: ['wolfram', 'documents'] }),
      expected_version: 1,
    })
    expect(saved).toHaveBeenCalledWith('a1', ['wolfram', 'documents'], 1)
  })

  it('落库失败就翻回去，并把原因说出去', async () => {
    const fail = vi.fn()
    vi.mocked(updateProjectAgent).mockRejectedValue(new Error('offline'))
    const s = skills(fail)
    await s.openSkills()

    await s.adoptSkill('documents')

    expect(s.carried.value).toEqual(['wolfram'])
    expect(fail).toHaveBeenCalledWith('offline')
  })

  it('保存时版本过期：409 翻回这一颗、说一声，并请父组件重读', async () => {
    const fail = vi.fn()
    const stale = vi.fn()
    vi.mocked(updateProjectAgent).mockRejectedValue(new ApiError(409, 'conflict'))
    const s = skills(fail, undefined, ['wolfram'], stale)
    await s.openSkills()

    await s.adoptSkill('documents')

    expect(s.carried.value).toEqual(['wolfram'])
    expect(fail).toHaveBeenCalledWith(t('work.room.skills.stale'))
    expect(stale).toHaveBeenCalled()
  })

  it('打开面板占一把：占到了就是没人在编辑', async () => {
    const s = skills()
    await s.holdAgent('a1')

    expect(setAgentEditing).toHaveBeenCalledWith('p1', 'a1', true)
    expect(s.editingBlocked.value).toBe(false)
  })

  it('别人正占着：占不到就转只读，并说一声', async () => {
    const fail = vi.fn()
    vi.mocked(setAgentEditing).mockRejectedValue(new ApiError(409, 'conflict'))
    const s = skills(fail)
    await s.holdAgent('a1')

    expect(s.editingBlocked.value).toBe(true)
    expect(fail).toHaveBeenCalled()
  })

  it('关掉面板放掉占用', async () => {
    const s = skills()
    await s.releaseAgent('a1')

    expect(setAgentEditing).toHaveBeenCalledWith('p1', 'a1', false)
    expect(s.editingBlocked.value).toBe(false)
  })

  it('带了八颗再加一颗：没有上限，整份名单落库', async () => {
    const saved = vi.fn()
    const eight = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h']
    const s = skills(() => {}, saved, eight)
    await s.openSkills()

    await s.adoptSkill('wolfram')

    expect(updateProjectAgent).toHaveBeenCalledWith('p1', 'a1', {
      configuration: expect.objectContaining({ skills: [...eight, 'wolfram'] }),
      expected_version: 1,
    })
    expect(s.carried.value).toHaveLength(9)
  })

  it('健康探不到只是那一颗是灰的，列表照常出来', async () => {
    vi.mocked(getSkillHealth).mockRejectedValue(new Error('boom'))
    const s = skills()

    await s.openSkills()

    expect(s.pool.value).toHaveLength(4)
    expect(s.skillHealth.value.wolfram).toBeUndefined()
  })

  it('互联网直连：自动安装并带上当前这位队友', async () => {
    vi.mocked(fetchMachineSkill).mockResolvedValue({
      landed: true,
      plan: [{ name: 'pdf', description: '读 PDF', files: ['SKILL.md'] }],
      skills: [
        {
          name: 'pdf',
          description: '读 PDF',
          source_root: '/home/me/.cheese/web-skills/pdf',
          digest: 'x',
          state: 'seen',
          adopted: false,
        },
      ],
      problems: ['docs/x.txt: 不是文本'],
      roots: ['/home/me/.cheese/web-skills'],
      workspaces: [],
    } as never)
    const s = skills()
    await s.openSkills()

    await s.fetchWebSkill('https://github.com/anthropics/skills/tree/main/skills/pdf')

    expect(fetchMachineSkill).toHaveBeenLastCalledWith(
      'dev2',
      'https://github.com/anthropics/skills/tree/main/skills/pdf',
      'p1',
      'skill'
    )
    expect(s.machineSkills.value.map((m) => m.name)).toEqual(['pdf'])
    expect(s.machineProblems.value).toEqual(['docs/x.txt: 不是文本'])
    expect(s.workspaceRoots.value).toEqual([])
    expect(updateProjectAgent).toHaveBeenCalledWith('p1', 'a1', {
      configuration: expect.objectContaining({ skills: ['wolfram', 'pdf'] }),
      expected_version: 1,
    })
    expect(s.carried.value).toEqual(['wolfram', 'pdf'])
  })

  it('市场：打开面板就拉一次，卡片按 kind 分三堆', async () => {
    const s = skills()

    await s.openSkills()
    await vi.waitFor(() => expect(s.marketLoading.value).toBe(false))

    expect(fetchSkillMarket).toHaveBeenCalledTimes(1)
    expect(s.market.value?.skills.map((c) => c.name)).toEqual(['pdf'])
    expect(s.market.value?.plugins.map((c) => c.name)).toEqual(['reviewer'])
    expect(s.market.value?.plugin_total).toBe(315)
    expect(s.market.value?.mcp.map((c) => c.name)).toEqual(['Acme'])
  })

  it('市场读不到：说一声，机器那一段照常', async () => {
    vi.mocked(fetchSkillMarket).mockRejectedValue(new Error('offline'))
    const fail = vi.fn()
    const s = skills(fail)

    await s.openSkills()
    await vi.waitFor(() => expect(s.marketLoading.value).toBe(false))

    expect(s.market.value).toBeNull()
    expect(fail).toHaveBeenCalledWith('offline')
    expect(s.machineSkills.value.map((m) => m.name)).toEqual(['ponytail', 'draft-skill'])
  })

  it('装一枚插件：kind 跟着地址发上去并自动安装', async () => {
    vi.mocked(fetchMachineSkill).mockResolvedValue({
      landed: true,
      plan: [],
      skills: [],
      problems: [],
      roots: [],
      workspaces: [],
    } as never)
    const s = skills()
    await s.openSkills()

    await s.fetchWebSkill('https://github.com/o/r/tree/main/plugins/reviewer', 'plugin')

    expect(fetchMachineSkill).toHaveBeenCalledWith(
      'dev2',
      'https://github.com/o/r/tree/main/plugins/reviewer',
      'p1',
      'plugin'
    )
  })

  it('互联网直连失败：走 onError，已经拿到的列表不动', async () => {
    vi.mocked(fetchMachineSkill).mockRejectedValue(new Error('skillWebSpec'))
    const fail = vi.fn()
    const s = skills(fail)
    await s.openSkills()

    await s.fetchWebSkill('https://example.com/x')

    expect(fail).toHaveBeenCalledWith('skillWebSpec')
    expect(s.machineSkills.value.map((m) => m.name)).toEqual(['ponytail', 'draft-skill'])
  })
})
