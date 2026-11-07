// 技能面板：**这个队友**带哪些技能、还能从哪儿加。
//
// 主语是「一个队友」（设计稿 F1）：技能跟着人走，学术研究的队友不该继承全栈工程队友
// 的构建约定。技能名单落在队友自己身上（`agent_instances.configuration.skills`）。
//
// 可加的技能有三个来源，合成一个池子画：
//   * 平台发货（`/projects/{id}/skill-catalog`）
//   * **我自己**机器上的（`/connector/my/devices/{id}/skills`）—— 不是房间那台：读别人
//     的机器是隐私上的越界，别人的盘不关我的事。索引里有的就能勾，不再先「采纳」一道。
//   * 我导入的（项目技能，`/projects/{id}/skills`）
//
// **打开面板时才读**，不做常驻拉取：没人打开的时候多读一次，只是一次没人看的网络
// 调用。健康那一列同理，一次打开探一遍（后端自己还缓存 5 分钟），而且只探平台那几颗
// ——别的来源后端没有探针，画成「还没探到」比编一个绿点诚实。
import type { MachineSkill, SkillMarket } from '../api/machineSkills'
import type { SkillHealth } from '../api/projectSkills'
import type { ProjectAgent } from '../cx_types'

import { computed, ref } from 'vue'

import { fetchMachineSkill, fetchSkillMarket, listMachineSkills } from '../api/machineSkills'
import { getSkillHealth, listProjectSkills, listSkillCatalog } from '../api/projectSkills'

import { listMyDevices, setAgentEditing, updateProjectAgent } from '@/api'
import { ApiError } from '@/api/http'
import { t } from '@/i18n'
import { pickMyDevice } from '@/lib/myDevice'

export type SkillSource = 'platform' | 'machine' | 'project'

export interface SkillItem {
  name: string
  description: string
  source: SkillSource
}

function dedupe(items: SkillItem[]): SkillItem[] {
  const seen = new Set<string>()
  return items.filter((item) => (seen.has(item.name) ? false : (seen.add(item.name), true)))
}

export function useAgentSkills(
  projectId: () => string | null,
  agent: () => ProjectAgent | null,
  onError: (message: string) => void,
  onSaved?: (agentId: string, skills: string[], version: number) => void,
  onStale?: () => void
) {
  const pool = ref<SkillItem[]>([])
  const skillHealth = ref<Record<string, SkillHealth>>({})
  // 我自己的机器，以及它盘上的技能索引。deviceId 为 null = 没有机器可读。
  const deviceId = ref<string | null>(null)
  const machineSkills = ref<MachineSkill[]>([])
  const machineProblems = ref<string[]>([])
  // 人登记的工作区根（`<根>/.trae/skills` 也一起扫）。清单存在机器上，扫一次才知道。
  const workspaceRoots = ref<string[]>([])
  // 市场：两家厂商公开的技能 / 插件 / MCP。公开目录，和哪台机器无关。
  const market = ref<SkillMarket | null>(null)
  const marketLoading = ref(false)
  // 面板自己的名单：打开时从队友身上抄一份，保存后就地更新，父组件不必重拉。
  const carriedNames = ref<string[]>([])
  const loading = ref(false)
  const scanning = ref(false)
  const saving = ref(false)
  // 改一个队友带的技能，是项目里每个人都做得的（后端 `projects_agents.py` 不再要
  // manager，见 main 的 `say_agent_changes`）；真正拦住丢更新的是保存带上的
  // `expected_version` 和别人正占着的编辑租约。所以这里恒为可改，不再去 default-model
  // 读一个后端从来不发、类型里也没有的 `can_manage`。
  const canManage = ref(true)
  // 「正在编辑」的占用：打开面板时占一把，关掉就放掉。占不到（别人拿着）就把面板转只读，
  // 并把持有者记下来画出来。这是礼貌性提示，不是锁 —— 真正拦住丢更新的是保存带上去的
  // `expected_version`（409）。Valkey 没配时后端一律答「没人编辑」，这里自然也不拦。
  const editingBlocked = ref(false)
  const editingBy = ref<string | null>(null)

  // 面板的「这一场」：每次打开、每次关掉都换一场。异步回包落地前要对上发起时的那一场
  // 和那一位——只比队友 id 拦不住 A→B→A：重新打开的 A 和第一次打开的 A 是同一个 id，
  // 第一次的回包晚到就会盖上来。
  let session = 0
  function boundTo(pid: string, agentId: string) {
    const mine = session
    return () => session === mine && projectId() === pid && agent()?.id === agentId
  }

  const carried = computed<string[]>(() => carriedNames.value)

  /** 带上的技能，连池子里查不到的也画出来（名字认得、说明欠奉），从格子里消失更糟。 */
  const carriedItems = computed<SkillItem[]>(() =>
    carriedNames.value.map(
      (name) => pool.value.find((p) => p.name === name) ?? { name, description: '', source: 'platform' }
    )
  )

  /** 还能加的：池子里减掉已经带上的。 */
  const available = computed<SkillItem[]>(() => pool.value.filter((p) => !carriedNames.value.includes(p.name)))

  /** 打开面板：读池子、我的机器索引，顺手把平台那几颗的健康探一遍。 */
  async function openSkills() {
    const pid = projectId()
    const subject = agent()
    if (!pid || !subject) return
    session += 1
    // 这一趟读的是「打开时那一位」的池子：人关掉面板、换了队友或重新打开了，读回来的东西
    // 不能画到现在这块面板上（技能跟着人走，池子里的机器和项目那两段也是按人挑的）。
    const stillHere = boundTo(pid, subject.id)
    loading.value = true
    skillHealth.value = {}
    carriedNames.value = [...(subject.configuration.skills ?? [])]

    // 平台目录是面板的核心：这一条读不到就说一声，机器和项目那两段照常出来。
    const [catalog, project, devices] = await Promise.all([
      listSkillCatalog(pid).then(
        (r) => r.data,
        (e) => {
          onError(e instanceof Error ? e.message : t('work.room.skills.loadFailed'))
          return [] as { name: string; description: string }[]
        }
      ),
      listProjectSkills(pid).then(
        (r) => r.data,
        () => []
      ),
      listMyDevices().then(
        (r) => r.devices,
        () => []
      ),
    ])
    if (!stillHere()) return
    deviceId.value = pickMyDevice(devices)?.device_id ?? null

    machineSkills.value = []
    machineProblems.value = []
    workspaceRoots.value = []
    if (deviceId.value) {
      try {
        const scanned = (await listMachineSkills(deviceId.value)).skills
        if (stillHere()) machineSkills.value = scanned
      } catch {
        // 不是自己的机器、或者后端那一半还没上线：机器那一段空着，别的照常。
      }
      // 扫描成败都要等一个来回：回来时已经换场，下面拼出来的池子就不是现在这一位的。
      if (!stillHere()) return
    }
    // 市场：和哪台机器无关，但「装」要有个落点，所以等 deviceId 定下来才拉。不 await
    // ——它慢起来不该把整块面板按住。
    void openMarket()
    pool.value = dedupe([
      ...catalog.map((s): SkillItem => ({ name: s.name, description: s.description, source: 'platform' })),
      ...machineSkills.value.map((s): SkillItem => ({ name: s.name, description: s.description, source: 'machine' })),
      ...project
        .filter((s) => s.state === 'active')
        .map((s): SkillItem => ({ name: s.name, description: s.description, source: 'project' })),
    ])
    loading.value = false
    // 一颗探不到就说「未知」——不该让一颗探不到的把整个面板挡住。
    await Promise.all(
      catalog.map(async (s) => {
        try {
          const health = await getSkillHealth(pid, s.name)
          if (stillHere()) skillHealth.value = { ...skillHealth.value, [s.name]: health }
        } catch {
          // 未知：那一颗的圆点留着灰的，别的照常。
        }
      })
    )
  }

  /** 把这份名单写到这个队友身上。先改本地再落库，落库失败就翻回去。 */
  async function saveCarried(next: string[]) {
    const pid = projectId()
    const subject = agent()
    if (!pid || !subject || saving.value) return
    const before = [...carriedNames.value]
    carriedNames.value = next
    saving.value = true
    // 这一笔写的是「发起时那一场的那一位」：换场之后，它的结果不能画到现在的面板上。
    const stillHere = boundTo(pid, subject.id)
    try {
      const updated = await updateProjectAgent(pid, subject.id, {
        configuration: { ...subject.configuration, skills: next },
        // The version this panel was built from. Somebody saving in between makes
        // this one a 409 instead of a silent overwrite of their change.
        expected_version: subject.version,
      })
      // 这一笔把 version 顶了一格，而下一次保存还要靠它：把新值连同是哪一位交给父组件，
      // 否则下一颗就白撞一次 409。换了场也要交——落库是真的成功了，关掉再打开这一位时
      // 版本号要接得上；父组件按队友 id 记，不会把面板拉回这一位。
      if (projectId() === pid) onSaved?.(subject.id, next, updated.version)
    } catch (e) {
      // 名单只在还是这一场时翻回去；没存上这件事照样要说：这一笔是人自己点的，关掉面板或
      // 换了队友不等于不想知道它失败了。
      if (stillHere()) carriedNames.value = before
      if (e instanceof ApiError && e.status === 409) {
        onError(t('work.room.skills.stale'))
        // Re-read so the next attempt carries the version that won.
        onStale?.()
      } else {
        onError(e instanceof Error ? e.message : t('work.room.skills.saveFailed'))
      }
    } finally {
      saving.value = false
    }
  }

  /** 加 / 减一颗。一颗按钮不该等一个来回。 */
  async function setSkill(name: string, on: boolean) {
    const before = [...carriedNames.value]
    const next = on ? [...before, name] : before.filter((n) => n !== name)
    await saveCarried(next)
  }

  /** 占住这个队友（打开技能面板时）。占不到就把持有者记下来，面板转只读。 */
  async function holdAgent(agentId: string) {
    const pid = projectId()
    if (!pid) return
    // 还没回来人就关掉、换了队友或重新打开了：这一句说的是上一场的占用，不能画到现在这块面板上。
    const stillHere = boundTo(pid, agentId)
    editingBlocked.value = false
    editingBy.value = null
    try {
      const got = await setAgentEditing(pid, agentId, true)
      if (!stillHere()) return
      editingBy.value = got.editing?.handle ?? null
    } catch (e) {
      if (!stillHere()) return
      // 409 = 别人正拿着。名字从打开时那一行读，并如实说一声 —— 面板这一轮只读。
      editingBlocked.value = true
      editingBy.value = agent()?.editing ?? null
      onError(e instanceof Error ? e.message : t('work.room.skills.editingBlocked'))
    }
  }

  /** 放掉占用（关掉面板时）。放不掉也不打扰人：租约自己会过期。 */
  async function releaseAgent(agentId: string) {
    const pid = projectId()
    session += 1
    editingBlocked.value = false
    editingBy.value = null
    if (!pid) return
    try {
      await setAgentEditing(pid, agentId, false)
    } catch {
      // 面板已经关了，报错没有落点；租约到点自己释放。
    }
  }

  /** 从网上取一份技能：按 agentskills.io 规范检验后直接安装，并装备给当前队友。 */
  async function fetchWebSkill(url: string, kind: 'skill' | 'plugin' = 'skill') {
    const device = deviceId.value
    const pid = projectId()
    const subject = agent()
    if (!device || !pid || !subject || scanning.value) return
    // 装到机器上要等一阵：装完人已经换场了，就不能把它装备给现在面板上的那一位。技能已经
    // 落在机器上，下次打开面板会在池子里看到。
    const stillHere = boundTo(pid, subject.id)
    scanning.value = true
    try {
      const scan = await fetchMachineSkill(device, url, pid, kind)
      if (!stillHere()) return
      if (scan.landed) {
        machineSkills.value = scan.skills
        machineProblems.value = scan.problems
        workspaceRoots.value = scan.workspaces
        pool.value = dedupe([
          ...pool.value,
          ...scan.skills.map((s): SkillItem => ({ name: s.name, description: s.description, source: 'machine' })),
        ])
        const extra = scan.plan.map((p) => p.name).filter((n) => !carriedNames.value.includes(n))
        if (extra.length) await saveCarried([...carriedNames.value, ...extra])
      }
    } catch (e) {
      onError(e instanceof Error ? e.message : t('work.room.skills.fetchFailed'))
    } finally {
      scanning.value = false
    }
  }

  /** 读市场：两家厂商公开的技能 / 插件 / MCP。读不到就在那段说一声，别处照常。 */
  async function openMarket() {
    marketLoading.value = true
    try {
      market.value = await fetchSkillMarket()
    } catch (e) {
      market.value = null
      onError(e instanceof Error ? e.message : t('work.room.skills.marketFailed'))
    } finally {
      marketLoading.value = false
    }
  }

  return {
    pool,
    available,
    carriedItems,
    carried,
    skillHealth,
    machineSkills,
    machineProblems,
    workspaceRoots,
    market,
    marketLoading,
    editingBlocked,
    editingBy,
    deviceId,
    loading,
    scanning,
    saving,
    canManage,
    openSkills,
    openMarket,
    adoptSkill: (name: string) => setSkill(name, true),
    removeSkill: (name: string) => setSkill(name, false),
    holdAgent,
    releaseAgent,
    fetchWebSkill,
  }
}
