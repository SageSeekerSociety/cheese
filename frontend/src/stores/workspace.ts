import type { TopicNotifyLevel, TopicNotifySetting, TopicUnread } from '@/api'
import type { Project, Topic } from '@/cx_types'

import { computed, ref, toRef, watch } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { defineStore } from 'pinia'

import {
  archiveTopic,
  createTopic,
  joinChannel,
  leaveChannel,
  markAllTopicsRead,
  markTopicRead,
  setChannelDescription,
  setTopicNotifyLevel,
  setTopicTitle,
  unarchiveProject,
  unarchiveTopic,
  upgradeBlock,
} from '@/api'
import { ApiError, isProjectArchivedError } from '@/api'
import { setChannelMembersOnly } from '@/api/topicMembers'
import { t } from '@/i18n'
import { type ProjectRefusal, projectRefusal } from '@/lib/addresses'
import { memberName } from '@/lib/agentNames'
import { externalHandles } from '@/lib/externalMembers'
import { identityChanges } from '@/lib/identity'
import { myHandle } from '@/me'
import { patchQuery, queryClient, refreshQueries } from '@/query/client'
import { keys } from '@/query/keys'
import {
  membersQuery,
  notifyLevelsQuery,
  patchTopics,
  privateUnreadQuery,
  projectQuery,
  reading,
  refreshTopicRow as refreshRow,
  topicsQuery,
  unreadQuery,
} from '@/query/project'
import { projectsQuery, refreshProjects } from '@/query/projects'

// 当前打开的是哪个项目，和围着它的界面状态（栏宽、上次待的房间、为什么打不开）。
//
// 项目的数据（频道清单、成员、未读、通知档位）不存在这里：它们在 query 缓存里
// （`query/project.ts`），这里只是按当前项目把那几份接出来，写操作改的也是那几份。
//
// 它必须是 store 而不是 provide/inject：项目侧栏走全站的 `sidebar` 具名视图
// (App.vue)，和内容区是两个平级的组件实例，中间没有父子关系可以注入。这也正是
// 侧栏能常驻、内容区随便换页的原因。
const LAYOUT_KEY = 'cheesex.layout'

interface StoredLayout {
  chatPct?: number
  /** 右侧面板开着还是收着，这个人自己选过一次之后就照它来；没选过按宽度定。 */
  panelOpen?: boolean
  lastProjectId?: string
  /** 每个项目上次打开的话题 id：回到那个项目时，rail 那一格直接落回这个房间。 */
  lastTopicByProject?: Record<string, string>
}

function loadLayout(): StoredLayout {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(LAYOUT_KEY) || '{}')
    return typeof saved === 'object' && saved !== null ? (saved as StoredLayout) : {}
  } catch {
    return {} // malformed stored layout
  }
}

// 存进来的这份是整个浏览器一份，可能是老版本写的、也可能被人手改过：只留下
// projectId → topicId 都是字符串的那些，别的当没记过。
function validTopicMap(value: unknown): Record<string, string> {
  if (typeof value !== 'object' || value === null) return {}
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>).filter((entry): entry is [string, string] => {
      return typeof entry[1] === 'string'
    })
  )
}

const clampNum = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))

// 手机底栏「工作区」那一格要在冷启动时就知道该落到哪个项目，那时 store 里还没有
// 打开过任何项目——所以这个值从存储里直接读，不经过 store 实例。
export function lastOpenedProjectId(): string | null {
  return loadLayout().lastProjectId ?? null
}

export const useWorkspaceStore = defineStore('cxWorkspace', () => {
  const projectId = ref<string | null>(null)
  const pid = computed(() => projectId.value ?? '')
  // 地址里的项目没换成 UUID，守卫已经问过服务端为什么（`projectRefusal`）：不再拿短名去
  // 请求，那只会被当成参数不合法挡回来，侧栏上露出一句「请求参数不合法」。
  const refused = ref<ProjectRefusal | null>(null)
  // 跟着登录的人变：换了人，未读读的是新的人那一份。
  const me = computed(() => {
    void identityChanges.value
    return myHandle()
  })
  const hasProject = computed(() => !!projectId.value && !refused.value)

  // 进了项目、或者哪一页要了（`refreshProjects`）才读：没打开任何项目时 store 不替谁去问。
  const projectsWanted = ref(false)
  const projectsRead = useQuery(
    computed(() => ({ ...projectsQuery(), enabled: hasProject.value || projectsWanted.value })),
    queryClient
  )
  const projects = computed<Project[]>(() => projectsRead.data.value ?? [])
  // 「清单问过了」——成功、失败、还是空清单，都算问过。它和 `projects.length > 0`
  // 是两件事：后者只知道「手上有货」，前者的意思是「不会再变了，可以据此做决定了」。
  //
  // 壳跟着项目行走，而 WorkspaceEntry 要拿壳决定第一屏。分不开发，就会一直等一个
  // 不会来的答案；或者更坏，拿一个还没到货的清单当「这个项目没有壳」。
  const projectsSettled = computed(() => projectsRead.isFetched.value)

  const topicsRead = useQuery(
    computed(() => ({ ...topicsQuery(pid.value), enabled: hasProject.value })),
    queryClient
  )
  const topics = computed<Topic[]>(() => topicsRead.data.value?.data ?? [])
  const loadingTopics = computed(() => hasProject.value && topicsRead.isPending.value && !topicsRead.isError.value)

  const membersRead = useQuery(
    computed(() => ({ ...membersQuery(pid.value), enabled: hasProject.value })),
    queryClient
  )
  const members = computed(() => membersRead.data.value ?? [])
  // 名册上的外部成员（团队以外、被邀请进这个项目的人）。聊天署名、@ 候选、房间名册
  // 都拿它来挂「外部」那个标，所以放在 store 里算一次，谁问都是同一份。
  const externals = computed(() => externalHandles(members.value))
  function isExternal(handle: string | null | undefined): boolean {
    return !!handle && externals.value.has(handle)
  }
  // 界面上称呼项目 AI 队友用的名字。项目可以给它改名，所以任何一处都不能写死「芝士」；
  // 名册还没到时才退回「芝士」。房间里有自己的 AI 席位时，对话里读的是房间名册
  // （`useRoomRoster`），这里给的是项目默认那一位，供拿不到房间名册的地方用。
  const agentName = computed(
    () => memberName(members.value.find((m) => m.agent && m.project_default)) || t('shell.agentDefaultName')
  )
  // 同一位的 handle：句子里提到它时画成可点的 @chip（UserRef），点了去它的成员页。
  const agentHandle = computed(() => members.value.find((m) => m.agent && m.project_default)?.user_handle ?? null)

  // 每个频道和任务在等我的东西（`TopicUnread`：行上的数字、名字加不加粗、上次读后来了
  // 几条），和私聊未读——私聊按对方的 handle 编址（'cheese' = 和芝士那一间），因为私聊
  // 的行来自成员名单，没有话题 id。频道的数字已经按我设的通知档位算过了（后端）。
  const withMe = computed(() => hasProject.value && !!me.value)
  const unreadRead = useQuery(
    computed(() => ({ ...unreadQuery(pid.value, me.value), enabled: withMe.value })),
    queryClient
  )
  const unreadMap = computed<Record<string, TopicUnread>>(() => unreadRead.data.value ?? {})
  const privateUnreadRead = useQuery(
    computed(() => ({ ...privateUnreadQuery(pid.value, me.value), enabled: withMe.value })),
    queryClient
  )
  const privateUnreadMap = computed<Record<string, number>>(() => privateUnreadRead.data.value ?? {})
  // 我不在默认档位的频道（{topic_id: {level, muted_until}}）。过了期的静音后端不列；
  // 页面开着的时候静音到点，下一次轮询会把它拿掉。
  const levelsRead = useQuery(
    computed(() => ({ ...notifyLevelsQuery(pid.value), enabled: hasProject.value })),
    queryClient
  )
  const notifyLevels = computed<Record<string, TopicNotifySetting>>(() => levelsRead.data.value ?? {})
  function levelOf(topicId: string): TopicNotifyLevel {
    return notifyLevels.value[topicId]?.level ?? 'mentions'
  }
  function mutedUntil(topicId: string): string | null {
    return notifyLevels.value[topicId]?.muted_until ?? null
  }
  function isMuted(topicId: string): boolean {
    return levelOf(topicId) === 'mute'
  }

  // What the URL says is open. Set by the shell from the route, read here so a
  // background unread refresh never lights a badge on the thing you're reading.
  const activeTopicId = toRef(reading, 'topicId')
  const activeDmPeer = toRef(reading, 'dmPeer')

  const stored = loadLayout()
  const chatPct = ref(typeof stored.chatPct === 'number' ? stored.chatPct : 50)
  const panelPref = ref<boolean | null>(typeof stored.panelOpen === 'boolean' ? stored.panelOpen : null)
  // 每个项目上次打开的话题。rail 的项目格子用它落回那个房间，而不是每次都落在
  // 项目首页（每天都走的主路径不该多加一跳）。
  const lastTopicByProject = ref<Record<string, string>>(validTopicMap(stored.lastTopicByProject))
  function persistLayout() {
    localStorage.setItem(
      LAYOUT_KEY,
      JSON.stringify({
        chatPct: chatPct.value,
        panelOpen: panelPref.value ?? undefined,
        lastProjectId: projectId.value ?? undefined,
        lastTopicByProject: lastTopicByProject.value,
      })
    )
  }
  function setChatPct(pct: number) {
    chatPct.value = clampNum(pct, 25, 80)
    persistLayout()
  }
  function setPanelPref(open: boolean) {
    panelPref.value = open
    persistLayout()
  }

  /** 记住正待着的这个房间是**哪个项目**的：回到项目时 rail 那一格落回它。 */
  function rememberTopic(projectKey: string, topicId: string) {
    if (!projectKey || !topicId || lastTopicByProject.value[projectKey] === topicId) return
    lastTopicByProject.value = { ...lastTopicByProject.value, [projectKey]: topicId }
    persistLayout()
  }

  /**
   * 这个项目上次打开的话题；不在了就交回 null，落回项目首页。
   *
   * 「还在不在」在读到这个项目的清单时校验：记着的那个话题被删了、或被收成了别的
   * 房间，就不该再把人送进一个打不开的房间。
   */
  function lastTopicIdFor(projectKey: string): string | null {
    return lastTopicByProject.value[projectKey] ?? null
  }

  function forgetRememberedTopic(projectKey: string) {
    if (!(projectKey in lastTopicByProject.value)) return
    const next = { ...lastTopicByProject.value }
    delete next[projectKey]
    lastTopicByProject.value = next
    persistLayout()
  }

  const error = ref<string | null>(null)
  function reportError(e: unknown, fallback: string) {
    // 项目在这期间被所有者归档了：那不是一次失败，是这个项目换了状态，整块换成说明。
    if (isProjectArchivedError(e)) {
      denied.value = 'archived'
      return
    }
    error.value = e instanceof Error ? e.message : fallback
  }

  /**
   * 这个项目为什么打不开——**一个不会自己消失的状态**，不是那条 4 秒的红条。
   *
   * 非成员打开项目链接时，话题列表 401/403，而红条弹 4 秒就没了：之后页面上没有
   * 任何解释，话题列表空白、项目名也不显示，看起来跟「一个刚建好、还什么都没有
   * 的项目」一模一样。错过那 4 秒就再无线索。
   *
   * 两档分开，因为下一步动作不一样：没登录的人要去登录，登录了的人得去要权限。
   */
  const denied = ref<'unauthenticated' | 'forbidden' | 'archived' | null>(null)
  // 换了人（或者从没登录变成登录了）：上一个身份进不来，不代表这一个也进不来。
  watch(me, () => {
    denied.value = null
  })
  const accessDenied = computed<'unauthenticated' | 'forbidden' | 'missing' | 'archived' | null>(
    () => refused.value ?? denied.value
  )
  function noteAccess(e: unknown) {
    if (!(e instanceof ApiError)) return false
    if (e.status === 401) denied.value = 'unauthenticated'
    else if (e.status === 403) denied.value = 'forbidden'
    else return false
    return true
  }

  /**
   * 话题清单这一块没读到时服务端给的原因，交给侧栏**就地**显示 + 重试
   * （docs/design-system.md §3.10）。和上面那条 `error`（弹一条就走的全局 toast）
   * 分开：读不到话题清单要留在它读的那块地方——那条红条几秒就没，之后和「暂无
   * 话题」长得一模一样，人再也分不出是「坏了」还是「本来就没有」。手上有清单时，
   * 后台那一次没读到不算：清单维持原样。
   */
  const topicsError = computed<string | null>(() => {
    const e = topicsRead.error.value
    if (!e || topicsRead.data.value || accessDenied.value) return null
    return e instanceof Error ? e.message : t('shell.workspaceErrors.loadTopics')
  })
  // 「进不来」和「进来了但这一次没取到」是两件事：前者要一屏说明，后者是侧栏里那块
  // 就地报错。分不开的话，一次网络抖动会被写成「你没有权限」。读到一半登录过期、或者
  // 席位被拿掉，也是这里接住：屏幕上那份清单已经不是这个人能看的了。
  watch(
    () => topicsRead.error.value,
    (e) => {
      if (e) noteAccess(e)
    }
  )
  watch(
    () => topicsRead.data.value,
    (payload) => {
      const list = payload?.data
      if (!list || !projectId.value) return
      // 项目归档了没有，看它的总览房间：总览只会随项目一起归档（单独归档它会被后端拒），
      // 所以这一位不用多发一个请求就读得出来。
      if (list.some((topic) => topic.kind === 'root' && topic.status === 'archived')) denied.value = 'archived'
      // 读到的清单里没有记着的那个话题了：忘掉它，rail 这一格回落项目首页。
      const remembered = lastTopicByProject.value[projectId.value]
      if (remembered && list.length > 0 && !list.some((row) => row.id === remembered)) {
        forgetRememberedTopic(projectId.value)
      }
    }
  )

  // 这个项目本身（`projectQuery`）。只在它归档了时才要：归档了的项目不在 `projects` 那份
  // 清单里，名字和所有者得从这一份读。
  const projectRead = useQuery(
    computed(() => ({ ...projectQuery(pid.value), enabled: hasProject.value && accessDenied.value === 'archived' })),
    queryClient
  )
  const openedProject = computed<Project | null>(() => projectRead.data.value ?? null)

  const rootTopic = computed<Topic | null>(() => topics.value.find((t) => t.kind === 'root') ?? null)
  const projectName = computed<string>(
    () =>
      projects.value.find((p) => p.id === projectId.value)?.name ??
      (openedProject.value?.id === projectId.value ? openedProject.value.name : '')
  )

  /** 进一个项目。上一个项目的「打不开」「报错」不带过来；数据各自按项目读。 */
  function openProject(id: string) {
    if (projectId.value === id && !accessDenied.value) return
    // 回到上次打不开的同一个项目（从登录页回来、刚被请进来）：上次读到的是「进不来」，
    // 这一次按现在的身份重读。
    const retry = projectId.value === id
    projectId.value = id
    refused.value = projectRefusal(id) ?? null
    denied.value = null
    error.value = null
    persistLayout()
    if (retry) {
      void topicsRead.refetch()
      void membersRead.refetch()
      void refreshUnread()
    }
  }

  /** 侧栏那块「加载话题失败」的「重试」：再读一次当前项目的话题清单。 */
  async function reloadTopics() {
    await topicsRead.refetch()
  }

  /** 取消归档正开着的这个项目，然后当作第一次打开它，重新取一遍。 */
  async function unarchiveOpenProject(): Promise<boolean> {
    const id = projectId.value
    if (!id) return false
    try {
      await unarchiveProject(id)
    } catch (e) {
      error.value = e instanceof Error ? e.message : t('shell.workspaceErrors.unarchive')
      return false
    }
    denied.value = null
    await Promise.all([refreshProjectList(), queryClient.invalidateQueries({ queryKey: keys.project(id) })])
    return true
  }

  /** 项目清单变了（新建、归档、转交、退出），或者这一页要用它：再读一次。 */
  function refreshProjectList(): Promise<void> {
    projectsWanted.value = true
    return refreshProjects()
  }

  function refreshTopics(): Promise<void> {
    return refreshQueries({ queryKey: keys.projectTopics(pid.value) })
  }

  function refreshMembers(): Promise<void> {
    return refreshQueries({ queryKey: keys.projectMembers(pid.value) })
  }

  /** 未读、私聊未读和通知档位：数字跟着档位变，三份一起问。 */
  function refreshUnread(): Promise<void> {
    const project = pid.value
    return Promise.all([
      refreshQueries({ queryKey: keys.projectUnread(project, me.value) }),
      refreshQueries({ queryKey: keys.projectPrivateUnread(project, me.value) }),
      refreshQueries({ queryKey: keys.projectNotifyLevels(project) }),
    ]).then(() => undefined)
  }

  /** 这一行变了：只取这一行补进清单（见 `query/project`）。 */
  function refreshTopicRow(topicId: string): Promise<void> {
    return refreshRow(pid.value, topicId)
  }

  /** 本地改了这几行：正在路上的那次单行读是改之前发出去的，也不能再补进来。 */
  async function patchRows(topicId: string, change: (topic: Topic) => Topic) {
    await queryClient.cancelQueries({ queryKey: keys.roomRow(topicId) })
    await patchTopics(pid.value, (rows) => rows.map((topic) => (topic.id === topicId ? change(topic) : topic)))
  }

  /** 改我对一个频道的通知档位（静音可以带截止时间）。先改本地，失败了改回去并说一声。 */
  async function setNotifyLevel(topicId: string, level: TopicNotifyLevel, until: string | null = null) {
    const key = keys.projectNotifyLevels(pid.value)
    // 只改、只还原这一个：别的频道这期间被轮询或另一次设置改过的，不跟着回滚。
    const before = notifyLevels.value[topicId]
    const put = (setting: TopicNotifySetting | undefined) =>
      patchQuery<Record<string, TopicNotifySetting>>(key, (levels) => {
        const next = { ...levels }
        if (setting && setting.level !== 'mentions') next[topicId] = setting
        else delete next[topicId]
        return next
      })
    await put({ level, muted_until: level === 'mute' ? until : null })
    try {
      await setTopicNotifyLevel(topicId, level, level === 'mute' ? until : null)
    } catch (e) {
      await put(before)
      reportError(e, t('work.room.menu.notifyFailed'))
    }
    // 数字跟着档位变，以服务器为准再拉一次。
    void refreshUnread()
  }

  /** 写频道说明（管理者）。成功之后这一行跟着变。 */
  async function describe(topicId: string, description: string): Promise<boolean> {
    try {
      const saved = await setChannelDescription(topicId, description)
      await patchRows(topicId, (topic) => ({ ...topic, description: saved.description ?? null }))
      return true
    } catch (e) {
      reportError(e, t('work.channel.describeFailed'))
      return false
    }
  }

  /** 设为私密 / 重新公开（管理者；公开只有项目管理员）。成功之后这一行跟着变。 */
  async function setMembersOnly(topicId: string, membersOnly: boolean): Promise<boolean> {
    try {
      const saved = await setChannelMembersOnly(topicId, membersOnly)
      await patchRows(topicId, (topic) => ({ ...topic, members_only: saved.members_only === true }))
      void refreshTopics()
      return true
    } catch (e) {
      reportError(e, t('work.channel.visibilityFailed'))
      return false
    }
  }

  /** 加入 / 退出一个频道。成功之后这一行的 `joined` 跟着变，侧栏随之出现或消失。 */
  async function setJoined(topicId: string, joined: boolean): Promise<boolean> {
    try {
      await (joined ? joinChannel(topicId) : leaveChannel(topicId))
    } catch (e) {
      reportError(e, t(joined ? 'work.channel.joinFailed' : 'work.channel.leaveFailed'))
      return false
    }
    await patchRows(topicId, (topic) => ({ ...topic, joined }))
    void refreshTopics()
    void refreshUnread()
    return true
  }

  /** 全部标为已读：先清掉本地角标，再让服务器把每一间的已读位推到现在。 */
  async function markAllRead() {
    const project = pid.value
    if (!project) return
    await patchQuery<Record<string, TopicUnread>>(keys.projectUnread(project, me.value), () => ({}))
    try {
      await markAllTopicsRead(project)
    } catch (e) {
      reportError(e, t('work.room.menu.markAllReadFailed'))
    }
    // 成功失败都以服务器为准再拉一次：失败了角标回来的是此刻真实的数，不是点之前那一份。
    void refreshUnread()
  }

  // Opening a topic = reading it: bump the server-side cursor and clear the
  // badge locally (optimistic — the next refresh agrees).
  //
  // `topicId` 是一段对话：频道自己的，或一个任务的（任务的未读只亮给负责人和协作者）。
  function markRead(topicId: string) {
    if (!me.value) return
    if (unreadMap.value[topicId] !== undefined) {
      void patchQuery<Record<string, TopicUnread>>(keys.projectUnread(pid.value, me.value), (map) => {
        const next = { ...map }
        delete next[topicId]
        return next
      })
    }
    markTopicRead(topicId, me.value).catch(() => {})
  }

  // Same, for a 私聊 — its badge is keyed by peer handle, not topic id.
  function markDmRead(topicId: string, peerKey: string) {
    if (!me.value) return
    if (privateUnreadMap.value[peerKey] !== undefined) {
      void patchQuery<Record<string, number>>(keys.projectPrivateUnread(pid.value, me.value), (map) => {
        const next = { ...map }
        delete next[peerKey]
        return next
      })
    }
    markTopicRead(topicId, me.value).catch(() => {})
  }

  async function renameTopic(topicId: string, title: string) {
    try {
      const updated = await setTopicTitle(topicId, title)
      await patchRows(topicId, (topic) => ({ ...topic, title: updated.title }))
    } catch (e) {
      reportError(e, t('shell.workspaceErrors.rename'))
    }
  }

  // 乐观：本地这一行立刻变成新的状态——菜单、列表、rail 当场就是改后的样子，省掉的
  // 是「点一下到界面动」之间那一次往返。服务端那份回来覆盖它；失败时还原并说一声。
  async function setStatus(topicId: string, status: Topic['status'], write: () => Promise<Topic>, failed: string) {
    const topic = topics.value.find((row) => row.id === topicId)
    // 归档的正是 rail 记着的那一个：就地忘掉（归档的话题还在清单里，读清单时的检查
    // 找不到它）。失败时这一步要退回去。
    const wasRemembered = status === 'archived' && !!topic && lastTopicByProject.value[topic.project_id] === topicId
    if (topic) {
      await patchRows(topicId, (row) => ({ ...row, status }))
      if (wasRemembered) forgetRememberedTopic(topic.project_id)
    }
    try {
      const updated = await write()
      await patchRows(topicId, (row) => ({ ...row, ...updated }))
      void refreshTopics()
    } catch (e) {
      if (topic) {
        await patchRows(topicId, (row) => ({ ...row, status: topic.status }))
        if (wasRemembered) rememberTopic(topic.project_id, topicId)
      }
      reportError(e, failed)
    }
  }

  function archive(topicId: string): Promise<void> {
    return setStatus(topicId, 'archived', () => archiveTopic(topicId), t('shell.workspaceErrors.archive'))
  }

  function unarchive(topicId: string): Promise<void> {
    return setStatus(topicId, 'active', () => unarchiveTopic(topicId), t('shell.workspaceErrors.unarchive'))
  }

  // The three ways a topic is born. Each returns the new topic so the caller can
  // navigate to it — creating a topic without opening it is never what was meant.
  // 房间是个群聊，建出来时坐着项目的默认队友；要请别的队友进来，和请人一样走
  // 成员名册。
  async function create(title: string, description = '', membersOnly = false): Promise<Topic | null> {
    const project = projectId.value
    const name = title.trim()
    if (!project || !name) return null
    try {
      const topic = await createTopic(project, name, description.trim() || undefined, membersOnly)
      if (projectId.value !== project) return null
      await patchTopics(project, (rows) => [topic, ...rows.filter((row) => row.id !== topic.id)])
      void refreshTopics()
      return topic
    } catch (e) {
      if (projectId.value === project) reportError(e, t('shell.workspaceErrors.createTopic'))
      return null
    }
  }

  /** 转为任务：频道里的一条消息变成这个频道的一个任务，返回任务的 id。 */
  async function upgradeMessage(messageId: string): Promise<string | null> {
    try {
      const made = await upgradeBlock(messageId)
      await refreshTopics()
      return made.id
    } catch (e) {
      reportError(e, t('shell.workspaceErrors.convertMessage'))
      return null
    }
  }

  return {
    projectId,
    projects,
    projectsSettled,
    accessDenied,
    projectRefused: computed(() => refused.value !== null),
    noteAccess,
    openedProject,
    topics,
    members,
    agentName,
    agentHandle,
    isExternal,
    loadingTopics,
    unreadMap,
    notifyLevels,
    levelOf,
    mutedUntil,
    isMuted,
    setNotifyLevel,
    setJoined,
    describe,
    setMembersOnly,
    markAllRead,
    privateUnreadMap,
    activeTopicId,
    activeDmPeer,
    chatPct,
    panelPref,
    error,
    topicsError,
    rootTopic,
    projectName,
    setChatPct,
    setPanelPref,
    reportError,
    refreshProjects: refreshProjectList,
    refreshMembers,
    refreshTopics,
    reloadTopics,
    refreshUnread,
    refreshTopicRow,
    openProject,
    unarchiveOpenProject,
    markRead,
    markDmRead,
    renameTopic,
    archive,
    unarchive,
    rememberTopic,
    lastTopicIdFor,
    create,
    upgradeMessage,
  }
})
