// 技能面板那点会算错的事：打开时从队友身上抄一份名单、把池子拼出来；加/减一颗之后
// 落库的是**这个队友带的全部名字**（整份覆盖，不是只发这一颗）；落库失败要翻回去；
// 超过上限要拦住；机器只认**我自己**的（在线的优先）。面板画成什么样这里不管。
import { ref } from 'vue'
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

/** 面板开着的时候人换了一个队友：技能面板本身不重建，`agent()` 换了指向。 */
function switchingSkills(carried = ['wolfram'], onSaved?: (id: string, s: string[], version: number) => void) {
  const subject = ref(agent(carried))
  const panel = useAgentSkills(
    () => 'p1',
    () => subject.value,
    () => {},
    onSaved
  )
  const switchTo = (id: string, skillsOf = ['wolfram']) => {
    subject.value = { ...agent(skillsOf), id, handle: id, seat_handle: id, display_name: id }
  }
  return { panel, switchTo }
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
    expect(saved).toHaveBeenCalledWith('a1', ['wolfram', 'documents'], 1, true)
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

  it('占用的回答回得比换人晚：不把上一位的名字画到这一位身上', async () => {
    let releaseFirst: () => void = () => {}
    const held = new Promise<void>((resolve) => (releaseFirst = resolve))
    vi.mocked(setAgentEditing)
      .mockImplementationOnce(async () => {
        await held
        throw new ApiError(409, 'conflict')
      })
      .mockResolvedValue({ editing: null } as never)

    const { panel, switchTo } = switchingSkills()
    const holdingFirst = panel.holdAgent('a1')
    switchTo('b2')
    await panel.holdAgent('b2')
    releaseFirst()
    await holdingFirst

    expect(panel.editingBlocked.value).toBe(false)
    expect(panel.editingBy.value).toBeNull()
  })

  it('快速切换队友：上一位的池子回得晚，不盖到这一位的面板上', async () => {
    let releaseFirst: () => void = () => {}
    const held = new Promise<void>((resolve) => (releaseFirst = resolve))
    vi.mocked(listSkillCatalog)
      .mockImplementationOnce(async () => {
        await held
        return { data: CATALOG, total: 2 } as never
      })
      .mockImplementation(async () => ({ data: [CATALOG[1]], total: 1 }) as never)

    const { panel, switchTo } = switchingSkills(['wolfram'])
    const openingFirst = panel.openSkills()
    switchTo('b2', ['documents'])
    await panel.openSkills()
    const secondPool = panel.pool.value.map((p) => p.name)
    expect(secondPool).not.toContain('wolfram')

    releaseFirst()
    await openingFirst

    expect(panel.carried.value).toEqual(['documents'])
    expect(panel.pool.value.map((p) => p.name)).toEqual(secondPool)
  })

  it('给上一位加技能的回包回得晚：不改这一位的名单，新版本按上一位的 id 交回', async () => {
    const saved = vi.fn()
    let releaseFirst: () => void = () => {}
    const held = new Promise<void>((resolve) => (releaseFirst = resolve))
    vi.mocked(updateProjectAgent).mockImplementationOnce(async () => {
      await held
      return { ...agent([]), version: 2 } as never
    })

    const { panel, switchTo } = switchingSkills(['wolfram'], saved)
    await panel.openSkills()
    const saving = panel.adoptSkill('documents')
    switchTo('b2', ['pdf'])
    await panel.openSkills()
    releaseFirst()
    await saving

    expect(panel.carried.value).toEqual(['pdf'])
    expect(saved).toHaveBeenCalledTimes(1)
    expect(saved).toHaveBeenCalledWith('a1', ['wolfram', 'documents'], 2, false)
  })

  it('网上取技能装完时人已换到另一位：不把它装备给现在这一位', async () => {
    let releaseScan: () => void = () => {}
    const held = new Promise<void>((resolve) => (releaseScan = resolve))
    vi.mocked(fetchMachineSkill).mockImplementationOnce(async () => {
      await held
      return {
        landed: true,
        plan: [{ name: 'pdf', description: '读 PDF', files: ['SKILL.md'] }],
        skills: [
          { name: 'pdf', description: '读 PDF', source_root: '/w/pdf', digest: 'x', state: 'seen', adopted: false },
        ],
        problems: [],
        roots: [],
        workspaces: [],
      } as never
    })

    const { panel, switchTo } = switchingSkills(['wolfram'])
    await panel.openSkills()
    const installing = panel.fetchWebSkill('https://github.com/anthropics/skills/tree/main/skills/pdf')
    await panel.releaseAgent('a1')
    switchTo('b2', ['documents'])
    await panel.openSkills()
    releaseScan()
    await installing

    expect(updateProjectAgent).not.toHaveBeenCalled()
    expect(panel.carried.value).toEqual(['documents'])
  })

  it('保存还没回来就关掉了面板、保存失败：照样告诉人没存上', async () => {
    const errors: string[] = []
    let releaseSave: () => void = () => {}
    const held = new Promise<void>((resolve) => (releaseSave = resolve))
    vi.mocked(updateProjectAgent).mockImplementationOnce(async () => {
      await held
      throw new Error('网络断了')
    })
    const subject = ref(agent(['wolfram']))
    const panel = useAgentSkills(
      () => 'p1',
      () => subject.value,
      (m) => errors.push(m)
    )

    await panel.openSkills()
    const saving = panel.adoptSkill('documents')
    await panel.releaseAgent('a1')
    releaseSave()
    await saving

    expect(errors).toEqual(['网络断了'])
  })

  it('上一位的机器扫描晚到并且失败：不把上一位的池子画到这一位身上', async () => {
    let failFirst: () => void = () => {}
    const held = new Promise<void>((resolve) => (failFirst = resolve))
    vi.mocked(listSkillCatalog)
      .mockResolvedValueOnce({ data: CATALOG, total: 2 } as never)
      .mockResolvedValue({ data: [CATALOG[1]], total: 1 } as never)
    let scanStarted: () => void = () => {}
    const scanning = new Promise<void>((resolve) => (scanStarted = resolve))
    vi.mocked(listMachineSkills)
      .mockImplementationOnce(async () => {
        scanStarted()
        await held
        throw new Error('机器离线')
      })
      .mockResolvedValue({ skills: [] } as never)

    const { panel, switchTo } = switchingSkills(['wolfram'])
    const openingFirst = panel.openSkills()
    await scanning
    await panel.releaseAgent('a1')
    switchTo('b2', ['documents'])
    await panel.openSkills()
    const secondPool = panel.pool.value.map((p) => p.name)
    expect(secondPool).toEqual(['documents'])

    failFirst()
    await openingFirst

    expect(panel.pool.value.map((p) => p.name)).toEqual(secondPool)
  })

  it('A→B→A：第一次打开 A 的池子回得晚，不盖住重新打开 A 之后的面板', async () => {
    let releaseFirst: () => void = () => {}
    const held = new Promise<void>((resolve) => (releaseFirst = resolve))
    vi.mocked(listSkillCatalog)
      .mockImplementationOnce(async () => {
        await held
        return { data: CATALOG, total: 2 } as never
      })
      .mockImplementation(async () => ({ data: [CATALOG[1]], total: 1 }) as never)

    const { panel, switchTo } = switchingSkills(['wolfram'])
    const openingFirst = panel.openSkills()
    await panel.releaseAgent('a1')
    switchTo('b2', ['documents'])
    await panel.openSkills()
    await panel.releaseAgent('b2')
    switchTo('a1', ['documents'])
    await panel.openSkills()
    const reopenedPool = panel.pool.value.map((p) => p.name)
    expect(reopenedPool).not.toContain('wolfram')

    releaseFirst()
    await openingFirst

    expect(panel.pool.value.map((p) => p.name)).toEqual(reopenedPool)
  })

  it('A→B→A：第一次占 A 的回答晚到说被占，不把重新打开的 A 标成只读', async () => {
    let releaseFirst: () => void = () => {}
    const held = new Promise<void>((resolve) => (releaseFirst = resolve))
    vi.mocked(setAgentEditing)
      .mockImplementationOnce(async () => {
        await held
        throw new ApiError(409, 'conflict')
      })
      .mockResolvedValue({ editing: null } as never)

    const { panel, switchTo } = switchingSkills()
    void panel.openSkills()
    const holdingFirst = panel.holdAgent('a1')
    await panel.releaseAgent('a1')
    switchTo('b2')
    void panel.openSkills()
    await panel.holdAgent('b2')
    await panel.releaseAgent('b2')
    switchTo('a1')
    void panel.openSkills()
    await panel.holdAgent('a1')
    releaseFirst()
    await holdingFirst

    expect(panel.editingBlocked.value).toBe(false)
    expect(panel.editingBy.value).toBeNull()
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

  it('市场读不到：失败和原因留给市场那块，不另弹提示，机器那一段照常', async () => {
    vi.mocked(fetchSkillMarket).mockRejectedValue(new Error('offline'))
    const fail = vi.fn()
    const s = skills(fail)

    await s.openSkills()
    await vi.waitFor(() => expect(s.marketLoading.value).toBe(false))

    expect(s.market.value).toBeNull()
    expect(s.marketFailed.value).toBe(true)
    expect(s.marketFailureReason.value).toBe('offline')
    expect(s.marketForbidden.value).toBe(false)
    expect(fail).not.toHaveBeenCalled()
    expect(s.machineSkills.value.map((m) => m.name)).toEqual(['ponytail', 'draft-skill'])
  })

  it('市场重读：开头清掉上一次的失败；正在读时再点一次，不会被第二份的结果搅乱', async () => {
    vi.mocked(fetchSkillMarket).mockRejectedValueOnce(new Error('offline'))
    const s = skills()
    await s.openSkills()
    await vi.waitFor(() => expect(s.marketFailed.value).toBe(true))

    // 在路上的这一份最终读到目录；要是又发了第二份，它会失败并把失败留在面板上。
    let finish: (v: unknown) => void = () => {}
    vi.mocked(fetchSkillMarket)
      .mockImplementationOnce(() => new Promise((r) => (finish = r)) as never)
      .mockRejectedValueOnce(new Error('重复请求'))
    void s.openMarket()
    void s.openMarket()

    expect(s.marketFailed.value).toBe(false)
    expect(s.marketLoading.value).toBe(true)
    finish({ ...MARKET_EMPTY })
    await vi.waitFor(() => expect(s.marketLoading.value).toBe(false))
    expect(s.market.value).not.toBeNull()
    expect(s.marketFailed.value).toBe(false)
    expect(s.marketFailureReason.value).toBeNull()
  })

  const MARKET_EMPTY = { skills: [], plugins: [], plugin_total: 0, mcp: [], notes: [] }

  it('市场 403：标成不给看', async () => {
    vi.mocked(fetchSkillMarket).mockRejectedValue(new ApiError(403, 'forbidden'))
    const s = skills()

    await s.openSkills()
    await vi.waitFor(() => expect(s.marketLoading.value).toBe(false))

    expect(s.marketForbidden.value).toBe(true)
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

  it('别人正占着、面板只读：网上取技能既不落到机器上，也不写队友的名单', async () => {
    vi.mocked(setAgentEditing).mockRejectedValue(new ApiError(409, 'conflict'))
    const s = skills()
    await s.openSkills()
    await s.holdAgent('a1')
    expect(s.editingBlocked.value).toBe(true)

    await s.fetchWebSkill('https://github.com/anthropics/skills/tree/main/skills/pdf')

    expect(fetchMachineSkill).not.toHaveBeenCalled()
    expect(updateProjectAgent).not.toHaveBeenCalled()
    expect(s.carried.value).toEqual(['wolfram'])
    expect(s.scanning.value).toBe(false)
  })

  it('取到一半才得知别人正占着：落盘那一步收不回，名单照样不写', async () => {
    let releaseScan: () => void = () => {}
    const held = new Promise<void>((resolve) => (releaseScan = resolve))
    vi.mocked(fetchMachineSkill).mockImplementationOnce(async () => {
      await held
      return {
        landed: true,
        plan: [{ name: 'pdf', description: '读 PDF', files: ['SKILL.md'] }],
        skills: [
          { name: 'pdf', description: '读 PDF', source_root: '/w/pdf', digest: 'x', state: 'seen', adopted: false },
        ],
        problems: [],
        roots: [],
        workspaces: [],
      } as never
    })
    let refuse: (e: unknown) => void = () => {}
    vi.mocked(setAgentEditing).mockImplementationOnce(() => new Promise((_, reject) => (refuse = reject)))
    const s = skills()
    await s.openSkills()
    const holding = s.holdAgent('a1')
    const installing = s.fetchWebSkill('https://github.com/anthropics/skills/tree/main/skills/pdf')
    refuse(new ApiError(409, 'conflict'))
    await holding
    releaseScan()
    await installing

    expect(fetchMachineSkill).toHaveBeenCalledTimes(1)
    expect(updateProjectAgent).not.toHaveBeenCalled()
    expect(s.carried.value).toEqual(['wolfram'])
  })
})
