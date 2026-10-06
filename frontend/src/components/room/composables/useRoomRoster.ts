/**
 * 「这个房间里都有谁，各自叫什么」——名册、座位、显示名、头像。
 *
 * 分成房间名册和项目名册两份不是历史包袱：**AI 队友的座位只在房间名册上**，一个
 * 房间一个分身（handle 是 `cheese-<话题 hex>`），项目名册上没有它。所以「它叫什么」
 * 要查房间那份，「这个人叫什么」要查项目那份，两张表都得在。
 *
 * 这里不认识消息，也不认识通知档位——那些是房间壳的事。这里只回答 handle → 名字。
 */

import type { MentionPoolEntry } from '../../../composables/useRoomMentionPicker'
import type { Block, ProjectMemberRow, Topic, TopicMemberRow } from '../../../cx_types'

import { computed, ref, watch } from 'vue'
import { tryOnScopeDispose } from '@vueuse/core'

import { t } from '../../../i18n'
import { agentNames, memberName } from '../../../lib/agentNames'
import { isAgentBlock } from '../../../lib/authorship'
import { isExternalMember } from '../../../lib/externalMembers'
import { cachedTopicPanel, fetchTopicMembers } from '../../../lib/topicPanelCache'
import { onTopicRosterChange } from '../../../lib/topicRosterChanges'
import { getAvatarUrl } from '../../../utils/materials'

export function useRoomRoster(options: {
  topic: () => Topic | null
  members: () => ProjectMemberRow[]
  /** 自己的登录 handle。 */
  author: string
  /** 名册拉不到时说给用户听——静默的表现是「@ 不出芝士」而屏幕上没有解释。 */
  onError: (message: string) => void
}) {
  // 这个房间里有谁。
  //
  // @ 的候选不能只看项目名册：**芝士的座位在话题名册上**，一个话题一个分身
  // （handle 是 cheese-<话题 hex>），项目名册上没有它——种子数据里有一行共用的
  // `cheese` 掩盖过这件事，真实部署里没有。名单里少了它，就没人 @ 得到它，而 @ 它
  // 正是叫它干活的唯一方式。
  const roomMembers = ref<TopicMemberRow[]>([])

  // 手上这份名单是**哪个房间**的。
  //
  // 名册按话题拉，切话题的那一瞬间上一份还在内存里。而「名单里没有 AI 队友」在两
  // 种状态下含义正相反：还没到（要等——此刻替人写的 @ 指不到这个房间那位）、到了
  // 确实没有（老话题没有自己的座位，得退回项目名册上那行共用的芝士）。一句「load
  // 完没完」的布尔分不开这两件事，所以记的是名单的主人。
  const rosterFor = ref<string | null>(null)
  const rosterLoaded = computed(() => {
    const here = options.topic()
    return !!here && rosterFor.value === here.id
  })

  async function loadRoster() {
    const place = options.topic()
    if (!place) {
      roomMembers.value = []
      rosterFor.value = null
      return
    }
    const id = place.id
    // 切回来过的房间：上次那份名单先顶上，@ 补全和署名不必等这一轮网络。
    const cached = cachedTopicPanel('members', id)
    if (cached && rosterFor.value !== id) {
      roomMembers.value = cached.data
      rosterFor.value = id
    }
    try {
      const payload = await fetchTopicMembers(id)
      if (options.topic()?.id === id) {
        roomMembers.value = payload.data
        rosterFor.value = id
      }
    } catch {
      // 名单拉不到就说出来：@ 补全会缺人（包括芝士）。静默的话，表现是「@ 不出
      // 芝士」，而屏幕上没有任何东西说明为什么。
      options.onError(t('work.room.roster.mentionLoadFailed'))
    }
  }

  watch(() => options.topic()?.id, loadRoster, { immediate: true })
  // 名册抽屉里加人、移人、改角色，这份副本跟着重拉：只在切话题时拉的话，刚加进来
  // 的队友要等切走再切回来才 @ 得到。重拉期间旧名单留着（`rosterFor` 不动），不闪。
  tryOnScopeDispose(
    onTopicRosterChange((topicId) => {
      if (options.topic()?.id === topicId) void loadRoster()
    })
  )

  // 名册那一行有三种形状：话题名册是 member_handle，项目名册是 user_handle，而 @
  // 补全名单已经把它们归一到 handle 了。这里只关心「它叫什么、它的 handle 是哪个」。
  type RosterRow = {
    name?: string
    name_source?: string
    handle?: string
    member_handle?: string
    user_handle?: string
  }
  function seatOf(row: RosterRow | null | undefined): { handle: string; label: string } | null {
    const handle = row?.member_handle || row?.user_handle || row?.handle
    return handle ? { handle, label: memberName(row) || handle } : null
  }

  // 这个房间名册上坐着的 AI 队友。
  //
  // 名册没到时是 null，不拿项目那位顶：顶上去的后果是消息里那个 @ 指到另一个队友，
  // 读的人以为叫了这个房间的这位。
  const roomAgentSeat = computed(() => (rosterLoaded.value ? seatOf(roomMembers.value.find((m) => m.agent)) : null))

  // 这个项目的默认队友：一间**没有 AI 席位**的老房间，后端解析出来的就是它
  // （`topic_membership/services.py` 的 `_project_agent_seat` 读 `default_agent_instance_id`）。
  // 不能拿「名册上第一个带 AI 标的」代替：那是建得最早的那一位，而停用默认队友时
  // 默认会改判给另一位（`agent_instance/services.py` 的 `deactivate`），于是刚退下去
  // 的那位排在最前——界面写着它的名字，答话的是别人，正是「换人没生效」那个报障。
  const projectDefaultAgent = computed(() => options.members().find((m) => m.agent && m.project_default) ?? null)

  // 这个房间现在交给的是哪个 AI 队友。名册那一行说了算（后端把芝士那一行的名字
  // 解析成当前队友的名字）。界面上任何一处写死「芝士」，换完队友都不会变，看起来
  // 就是「换人没生效」——这正是它被报上来的样子。
  //
  // 名册没到时是 null，两边都不猜：那一刻认谁都可能认成上一个房间那位，界面上说出
  // 的名字会是别人的，正文里写下的 @ 会指到另一个身份。名册到了、这个房间确实没有
  // AI 座位（座位是后来才有的，老话题没有）时，退回项目的**默认**队友——否则这个
  // 话题永远叫不动它。
  const agentSeat = computed(
    () => roomAgentSeat.value ?? (rosterLoaded.value ? seatOf(projectDefaultAgent.value) : null)
  )

  /** 界面上称呼它用的名字。名册没到时谁也不猜，就写「芝士」。 */
  const agentName = computed(() => agentSeat.value?.label || t('work.room.defaultAgentName'))

  /** @ 得到的人：这个频道里的，加上项目里还没加入这个频道的**人**（带 `outsideTopic`，排在后面）。 */
  const mentionPool = computed<MentionPoolEntry[]>(() => {
    // 名册没到（切话题的那一瞬间）房间那半就是空的：宁可少一行，也不能把**上一个
    // 房间**的座位留在名单里——那一位的名字也写着「芝士」，@ 出来却是个不在这儿的
    // handle。人在项目名册上照样 @ 得到，缺的只是这一个房间自己的那几行。
    const room = (rosterLoaded.value ? roomMembers.value : []).map((m) => ({
      handle: m.member_handle,
      label: memberName(m) || m.member_handle,
      agent: !!m.agent,
      external: isExternal(m.member_handle),
      role: m.role,
    }))
    const inRoom = new Set(room.map((r) => r.handle))
    // 不在这间房里的 AI 队友不列：它只在自己坐着的房间里被 @ 叫得动，在这里 @ 它
    // 什么也不会发生（后端点名只认这间房的席位），列出来就是一个点了没反应的名字。
    // 已停用的也不列：停用就是为了挡住新的活，补全菜单是派活的入口。
    //
    // 这些人 @ 得到：频道人人能读，被 @ 的人收得到通知，在支线里回得了话。只是他们
    // 没加入这个频道，所以排在频道里的人后面。名册没到时说不准谁在谁不在，那一刻不分。
    const rest = options
      .members()
      .filter((m) => !inRoom.has(m.user_handle) && !m.agent && m.active !== false)
      .map((m) => ({
        handle: m.user_handle,
        label: m.name || m.user_handle,
        agent: !!m.agent,
        external: isExternalMember(m),
        outsideTopic: rosterLoaded.value,
      }))
    return [...room, ...rest]
  })
  // 这个 handle 在项目里是不是外部成员——聊天署名、@ 候选挂「外部」那个标用。问的是
  // 项目名册（房间名册上没有来路这一栏）。
  function isExternal(handle: string): boolean {
    return isExternalMember(memberByHandle.value.get(handle))
  }
  // handle → 名册行。**不要**改用 mentionNames：那张表额外塞了 all/here 两个保留
  // 键（渲染成「所有人」「在线成员」），一个恰好叫 all 的用户会被显示成「所有人」。
  const memberByHandle = computed(() => {
    const map = new Map<string, ProjectMemberRow>()
    for (const row of options.members()) map.set(row.user_handle, row)
    return map
  })
  // handle → 这个房间名册上的那一行。AI 队友的座位只在房间名册上（项目名册那行
  // 共用的 `cheese` 不是它），所以 AI 的署名查这张表，不查 memberByHandle。
  const seatByHandle = computed(() => {
    const map = new Map<string, TopicMemberRow>()
    for (const row of roomMembers.value) map.set(row.member_handle, row)
    return map
  })
  // 消息里存的 author 是登录身份的 handle（后端有意固定成这个，防伪造），所以
  // 「显示成昵称」只能在这里做：查名册，查不到（退出项目的人、anonymous 兜底
  // 作者）就把 handle 原样显示出来。
  //
  // AI 的一条和人的一条是同一个规矩：署它的作者，不署「这个房间的那位」。一个房
  // 间可以先后交给两个队友，两个人的话都还在记录里，各自署各自的名。名册还没到
  // 时写「芝士」——那一刻界面上任何一处说出的名字都可能是上一个房间那位。
  // 房间名册上找不到它，说明说这句话的队友已经不在这个房间了（被移出，或者这条是
  // 人和人的私聊里平台自己写的），项目名册上还有它的名字；轮次帧、收件人写的是队友
  // 自己的 handle，也在项目名册上认。都认不出也不能把 `cheese-<hex>` 摆到屏幕上：那
  // 是管道，读的人只会当成乱码。身份分叉，显示不分叉。
  const agentNameMap = computed(() => agentNames(rosterLoaded.value ? roomMembers.value : [], options.members()))
  /** 这个 AI handle 的名字；认不出（或名册还没到）时是 null，兜底由调用方定。 */
  function agentNameOf(handle: string): string | null {
    if (!rosterLoaded.value) return null
    return agentNameMap.value.get(handle) ?? null
  }
  function agentDisplayName(handle: string): string {
    return agentNameOf(handle) || t('work.room.defaultAgentName')
  }
  function displayName(m: Block): string {
    if (isAgentBlock(m)) return agentDisplayName(m.author)
    return memberByHandle.value.get(m.author)?.name || m.author
  }
  // 真头像加载失败过的 handle —— 退回彩色首字母，不留破图。
  const avatarBroken = ref<Set<string>>(new Set())
  function avatarSrc(handle: string): string | null {
    if (avatarBroken.value.has(handle)) return null
    const id = memberByHandle.value.get(handle)?.avatar_id
    // 名册上没这个人、或这行没有头像时返回 null：宁可留一个按 handle 哈希、认得出
    // 是谁的色块，也不要 getAvatarUrl(undefined) 给陌生人配一张 /avatars/default。
    return id == null ? null : getAvatarUrl(id)
  }
  function onAvatarError(handle: string): void {
    if (avatarBroken.value.has(handle)) return
    avatarBroken.value = new Set(avatarBroken.value).add(handle)
  }
  // 自己在名册上的名字（发件箱那几行用它，因为它们还没有作者字段）。
  const myName = computed(() => memberByHandle.value.get(options.author)?.name || options.author)
  return {
    roomMembers,
    rosterLoaded,
    agentSeat,
    agentName,
    mentionPool,
    memberByHandle,
    seatByHandle,
    agentNameOf,
    agentDisplayName,
    displayName,
    isExternal,
    avatarSrc,
    onAvatarError,
    myName,
  }
}
