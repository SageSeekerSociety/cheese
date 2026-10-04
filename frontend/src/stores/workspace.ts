import type { Project, ProjectMemberRow, Topic } from '@/cx_types'

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import {
  archiveTopic,
  createTopic,
  getPrivateUnread,
  getProject,
  getTopic,
  getTopicUnread,
  listProjectMembers,
  listProjects,
  listTopics,
  markTopicRead,
  setTopicTitle,
  unarchiveProject,
  unarchiveTopic,
  undoTopicTitle,
  upgradeBlock,
} from '@/api'
import { ApiError, isProjectArchivedError } from '@/api'
import { t } from '@/i18n'
import { memberName } from '@/lib/agentNames'
import { cachedWindow, refreshBlockCache } from '@/lib/blockCache'
import { externalHandles } from '@/lib/externalMembers'
import { myHandle } from '@/me'

// 项目级状态 (P0 架构): 话题树、成员、未读、排序、栏宽——一份，供项目框架下的
// 所有页面共用。
//
// 它必须是 store 而不是 provide/inject：项目侧栏走全站的 `sidebar` 具名视图
// (App.vue)，和内容区是两个平级的组件实例，中间没有父子关系可以注入。这也正是
// 侧栏能常驻、内容区随便换页的原因。
const LAYOUT_KEY = 'cheesex.layout'

interface StoredLayout {
  chatPct?: number
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
  const projects = ref<Project[]>([])
  const projectsSettled = ref(false)
  const topics = ref<Topic[]>([])
  const members = ref<ProjectMemberRow[]>([])
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
  const loadingTopics = ref(false)
  let projectEpoch = 0
  let topicRevision = 0
  const pendingReads = new Map<string, Promise<unknown>>()
  const queuedReads = new Map<string, Promise<unknown>>()
  // One read in flight per key, and at most one queued behind it.
  //
  // A caller asking while a read is in flight cannot take that read's answer:
  // the request left before whatever the caller is reacting to (a room renamed
  // by the platform, a new unread message) and can answer with things as they
  // were. So it waits for that read and reads once more; everyone who asks in
  // the meantime shares that second read.
  function readLatest<T>(key: string, read: () => Promise<T>): Promise<T> {
    const scopedKey = `${projectEpoch}:${key}`
    const pending = pendingReads.get(scopedKey)
    if (pending) {
      let queued = queuedReads.get(scopedKey)
      if (!queued) {
        queued = pending
          .catch(() => undefined)
          .then(() => {
            queuedReads.delete(scopedKey)
            return readLatest(key, read)
          })
        queuedReads.set(scopedKey, queued)
      }
      return queued as Promise<T>
    }
    const request = read().finally(() => pendingReads.delete(scopedKey))
    pendingReads.set(scopedKey, request)
    return request
  }

  // 话题列表排序: most-recently-active first, and no longer configurable. The
  // rail states this ordering by position alone — it used to also print
  // relTime(last_activity_at) on every row, which was the same fact a second
  // time, so the number went and the order stayed. The 排序 menu that used to
  // change it offered 标题 A→Z, which only ever reordered siblings inside the
  // tree; it was removed rather than kept as a control nobody could get value
  // out of.
  const TOPIC_SORT = { sort: 'last_activity_at', order: 'desc' } as const

  // 话题级未读 (Feishu-style badges), and 私聊未读 keyed by peer handle
  // ('cheese' = the 芝士 DM) — DM rows come from the roster and carry no topic id.
  const unreadMap = ref<Record<string, number>>({})
  const privateUnreadMap = ref<Record<string, number>>({})

  // What the URL says is open. Set by the shell from the route, read here so a
  // background unread refresh never lights a badge on the thing you're reading.
  const activeTopicId = ref<string | null>(null)
  const activeDmPeer = ref<string | null>(null)

  const stored = loadLayout()
  const chatPct = ref(typeof stored.chatPct === 'number' ? stored.chatPct : 50)
  // 每个项目上次打开的话题。rail 的项目格子用它落回那个房间，而不是每次都落在
  // 项目首页（每天都走的主路径不该多加一跳）。
  const lastTopicByProject = ref<Record<string, string>>(validTopicMap(stored.lastTopicByProject))
  function persistLayout() {
    localStorage.setItem(
      LAYOUT_KEY,
      JSON.stringify({
        chatPct: chatPct.value,
        lastProjectId: projectId.value ?? undefined,
        lastTopicByProject: lastTopicByProject.value,
      })
    )
  }
  function setChatPct(pct: number) {
    chatPct.value = clampNum(pct, 25, 80)
    persistLayout()
  }

  /** 记住正待着的这个房间是**哪个项目**的：回到项目时 rail 那一格落回它。 */
  function rememberTopic(pid: string, topicId: string) {
    if (!pid || !topicId || lastTopicByProject.value[pid] === topicId) return
    lastTopicByProject.value = { ...lastTopicByProject.value, [pid]: topicId }
    persistLayout()
  }

  /**
   * 这个项目上次打开的话题；不在了就交回 null，落回项目首页。
   *
   * 「还在不在」在第一次读到这个项目的清单时校验（`forgetMissingTopic`）：记着的
   * 那个话题被删了、或被收成了别的房间，就不该再把人送进一个打不开的房间。
   */
  function lastTopicIdFor(pid: string): string | null {
    return lastTopicByProject.value[pid] ?? null
  }

  function forgetRememberedTopic(pid: string) {
    if (!(pid in lastTopicByProject.value)) return
    const next = { ...lastTopicByProject.value }
    delete next[pid]
    lastTopicByProject.value = next
    persistLayout()
  }

  /** 刚读到的清单里没有记着的那个话题了：忘掉它，这一格回落项目首页。 */
  function forgetMissingTopic(pid: string, list: Topic[]) {
    const remembered = lastTopicByProject.value[pid]
    if (!remembered || list.length === 0 || list.some((row) => row.id === remembered)) return
    forgetRememberedTopic(pid)
  }

  const error = ref<string | null>(null)
  function reportError(e: unknown, fallback: string) {
    // 项目在这期间被所有者归档了：那不是一次失败，是这个项目换了状态，整块换成说明。
    if (isProjectArchivedError(e)) {
      accessDenied.value = 'archived'
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
  const accessDenied = ref<'unauthenticated' | 'forbidden' | 'archived' | null>(null)

  /**
   * 正在打开的这个项目本身，只在它已归档时才去取：归档了的项目不在 `projects` 那份
   * 清单里（清单只列在用的），名字和所有者得从这一份读。
   */
  const openedProject = ref<Project | null>(null)

  /**
   * 项目归档了没有，看它的总览房间：总览只会随项目一起归档（单独归档它会被后端拒），
   * 所以这一位不用多发一个请求就读得出来。
   */
  function noteArchived(list: Topic[]) {
    if (list.some((t) => t.kind === 'root' && t.status === 'archived')) accessDenied.value = 'archived'
  }
  function noteAccess(e: unknown) {
    if (!(e instanceof ApiError)) return false
    if (e.status === 401) accessDenied.value = 'unauthenticated'
    else if (e.status === 403) accessDenied.value = 'forbidden'
    else return false
    return true
  }

  const rootTopic = computed<Topic | null>(() => topics.value.find((t) => t.kind === 'root') ?? null)

  // 正在取的那些，用来区分「还没取到」和「取到了，不存在」——少了它，深链接进
  // 一个房间的第一帧会闪一下「这个话题不存在」。
  const resolvingPlaces = ref<Record<string, boolean>>({})

  /** 按 id 打开一个房间。已经在列表里就不用去问了。 */
  async function loadPlace(placeId: string): Promise<void> {
    if (!placeId) return
    if (topics.value.some((t) => t.id === placeId)) return
    if (resolvingPlaces.value[placeId]) return
    const pid = projectId.value
    const epoch = projectEpoch
    resolvingPlaces.value = { ...resolvingPlaces.value, [placeId]: true }
    try {
      const place = await getTopic(placeId)
      if (epoch !== projectEpoch || place.project_id !== pid) return
      if (!topics.value.some((t) => t.id === place.id)) topics.value.push(place)
    } catch {
      // 取不到就是不存在（或没权限）——视图那边照旧显示空状态。
    } finally {
      if (epoch === projectEpoch) {
        const next = { ...resolvingPlaces.value }
        delete next[placeId]
        resolvingPlaces.value = next
      }
    }
  }

  /** 这个 id 指向的房间。 */
  function placeById(placeId: string): Topic | null {
    return topics.value.find((t) => t.id === placeId) ?? null
  }

  function isResolvingPlace(placeId: string): boolean {
    return !!resolvingPlaces.value[placeId]
  }
  const projectName = computed<string>(
    () =>
      projects.value.find((p) => p.id === projectId.value)?.name ??
      (openedProject.value?.id === projectId.value ? openedProject.value.name : '')
  )

  // 「清单问过了」——成功、失败、还是空清单，都算问过。它和 `projects.length > 0`
  // 是两件事：后者只知道「手上有货」，前者的意思是「不会再变了，可以据此做决定了」。
  //
  // 壳跟着项目行走，而 WorkspaceEntry 要拿壳决定第一屏。分不开发，就会一直等一个
  // 不会来的答案；或者更坏，拿一个还没到货的清单当「这个项目没有壳」。
  async function refreshProjects() {
    try {
      projects.value = (await listProjects()).data
    } catch (e) {
      reportError(e, t('shell.workspaceErrors.loadProject'))
    } finally {
      projectsSettled.value = true
    }
  }

  async function refreshMembers() {
    const pid = projectId.value
    const epoch = projectEpoch
    if (!pid) return
    try {
      const payload = await readLatest(`members:${pid}`, () => listProjectMembers(pid))
      if (epoch === projectEpoch && projectId.value === pid) members.value = payload.data
    } catch {
      // Best-effort; the roster-driven menus just stay empty.
    }
  }

  // Silent refresh of the topic list (no spinner), so sub-topics 芝士 splits off
  // show up on their own.
  //
  // 这是 ProjectShell 那条 30 秒轮询走的路。`listTopics` 内部带条件请求：清单没变服务
  // 端回 304，我们拿回的还是上一次那同一个 payload 对象（见 api.listTopics）。这时候
  // `topics.value === payload.data` 已经成立，整段就跳过去 —— 不换数组，不为一份逐字节
  // 一样的数据把侧栏重画一遍。真的变了才落新的一份。
  async function refreshTopics() {
    const revision = topicRevision
    const pid = projectId.value
    const epoch = projectEpoch
    if (!pid) return
    try {
      const payload = await readLatest(`topics:${pid}:${revision}`, () => listTopics(pid, TOPIC_SORT))
      if (epoch === projectEpoch && projectId.value === pid && revision === topicRevision) {
        if (topics.value !== payload.data) topics.value = payload.data
        forgetMissingTopic(pid, payload.data)
        noteArchived(payload.data)
      }
    } catch {
      // Best-effort background refresh; ignore.
    }
  }

  // Enter a project: everything project-scoped is reloaded, and anything left
  // over from the previous project is dropped rather than shown as this one's.
  async function openProject(id: string) {
    const revision = topicRevision
    // Re-entering the project you were already in (back from 首页, a rail click):
    // the tree is still here and blanking it would flash the whole sidebar, but
    // it is as old as the time you spent away, so bring it up to date. Unless
    // the door was shut last time — back from 登录 there is no tree to keep, and
    // this shortcut would leave the 「需要登录」 screen up for a signed-in member.
    if (projectId.value === id && !accessDenied.value) {
      void refreshTopics()
      void refreshMembers()
      void refreshUnread()
      return
    }
    projectEpoch += 1
    const epoch = projectEpoch
    projectId.value = id
    resolvingPlaces.value = {}
    error.value = null
    persistLayout()
    topics.value = []
    members.value = []
    unreadMap.value = {}
    privateUnreadMap.value = {}
    loadingTopics.value = true
    accessDenied.value = null
    openedProject.value = null
    void refreshMembers()
    if (projects.value.length === 0) void refreshProjects()
    try {
      const payload = await readLatest(`topics:${id}:${revision}`, () => listTopics(id, TOPIC_SORT))
      if (epoch !== projectEpoch || projectId.value !== id) return
      if (revision === topicRevision) topics.value = payload.data
      forgetMissingTopic(id, payload.data)
      noteArchived(payload.data)
    } catch (e) {
      // 「进不来」和「进来了但这一次没取到」是两件事：前者要一屏说明，后者是那条
      // 红条。分不开的话，一次网络抖动会被写成「你没有权限」。
      if (epoch !== projectEpoch || projectId.value !== id || noteAccess(e)) return
      reportError(e, t('shell.workspaceErrors.loadTopics'))
    } finally {
      if (epoch === projectEpoch && projectId.value === id) loadingTopics.value = false
    }
    void refreshUnread()
  }

  /** 已归档的项目本身（名字、所有者）。「项目已归档」那一屏打开时来取。 */
  async function loadOpenedProject() {
    const id = projectId.value
    const epoch = projectEpoch
    if (!id) return
    try {
      const project = await getProject(id)
      if (epoch === projectEpoch && projectId.value === id) openedProject.value = project
    } catch {
      // 取不到就只少了名字和「取消归档」那颗按钮；「项目已归档」照样说得出。
    }
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
    // 回到清单里了；清单先刷，否则 openProject 会以为这是另一个项目。
    await refreshProjects()
    projectId.value = null
    await openProject(id)
    return true
  }

  async function refreshUnread() {
    const pid = projectId.value
    const me = myHandle()
    const epoch = projectEpoch
    if (!pid || !me) return
    void refreshPrivateUnread(pid, me)
    try {
      const map = await readLatest(`unread:${pid}:${me}`, () => getTopicUnread(pid, me))
      if (epoch !== projectEpoch || projectId.value !== pid) return
      // The open topic is being read right now — its badge never shows.
      if (activeTopicId.value) delete map[activeTopicId.value]
      // Background-refresh the timeline cache of topics whose unread grew: by
      // the time the user switches back, the reply that landed while they were
      // away is already rendered on the first frame (no late pop-in).
      //
      // 只预取「已经有缓存」的话题，也就是这一趟真的开过的那几个。没缓存的话题
      // 下次打开本来就要拉一次，预取省不掉那一次，只是把它挪到了最不该发请求的
      // 时刻：刷新页面时 unreadMap 是空的，于是**每一个**有未读的话题都算「变多
      // 了」，一个两百多话题的项目会在同一瞬间打出几十个 GET /blocks，占满后端
      // 的数据库连接池——被挤掉的不只是这些预取，还有用户此刻真正在等的那个请求。
      for (const [tid, n] of Object.entries(map)) {
        if (n > (unreadMap.value[tid] ?? 0) && cachedWindow(tid)) void refreshBlockCache(tid)
      }
      unreadMap.value = map
    } catch {
      // Best-effort; badges just stay as they were.
    }
  }

  async function refreshPrivateUnread(pid: string, me: string) {
    const epoch = projectEpoch
    try {
      const map = await readLatest(`private-unread:${pid}:${me}`, () => getPrivateUnread(pid, me))
      if (epoch !== projectEpoch || projectId.value !== pid) return
      // The DM being read right now never shows a badge on itself.
      if (activeDmPeer.value) delete map[activeDmPeer.value]
      privateUnreadMap.value = map
    } catch {
      // Best-effort; badges just stay as they were.
    }
  }

  // Opening a topic = reading it: bump the server-side cursor and clear the
  // badge locally (optimistic — the next refresh agrees).
  //
  // 卡下的消息**故意**不计进未读（否则每条活说句话就把房间标红，红点变噪音）。
  function markRead(topicId: string) {
    const me = myHandle()
    if (!me) return
    if (unreadMap.value[topicId] !== undefined) {
      const next = { ...unreadMap.value }
      delete next[topicId]
      unreadMap.value = next
    }
    markTopicRead(topicId, me).catch(() => {})
  }

  // Same, for a 私聊 — its badge is keyed by peer handle, not topic id.
  function markDmRead(topicId: string, peerKey: string) {
    const me = myHandle()
    if (!me) return
    if (privateUnreadMap.value[peerKey] !== undefined) {
      const next = { ...privateUnreadMap.value }
      delete next[peerKey]
      privateUnreadMap.value = next
    }
    markTopicRead(topicId, me).catch(() => {})
  }

  // 拍板之后这个地点的状态会变 —— 重新取一次，补进它所在的那张表，头部的状态
  // 标才会跟着动。
  async function refreshTopicRow(topicId: string) {
    try {
      const place = await getTopic(topicId)
      const i = topics.value.findIndex((t) => t.id === topicId)
      if (i >= 0) topics.value[i] = place
    } catch {
      // ignore
    }
  }

  function applyTopic(updated: Topic) {
    const t = topics.value.find((x) => x.id === updated.id)
    if (t) {
      topicRevision += 1
      t.title = updated.title
      t.title_source = updated.title_source
    }
  }

  // 人起的名字：平台之后不会再自动改它（见后端 topic/naming.py）。
  async function renameTopic(topicId: string, title: string) {
    try {
      applyTopic(await setTopicTitle(topicId, title))
    } catch (e) {
      reportError(e, t('shell.workspaceErrors.rename'))
    }
  }

  /** 撤销房间里那条「标题自动更新为…」：原来的名字回来，并且算人定的。 */
  async function undoAutoTitle(topicId: string, eventId: string) {
    try {
      applyTopic(await undoTopicTitle(topicId, eventId))
    } catch (e) {
      reportError(e, t('shell.workspaceErrors.undo'))
    }
  }

  // 归档 / 取消归档是就地发生的一件事：点下去这一行立刻挪进（或挪出）「已归档」，
  // 不等一次往返。服务器回来再落实；失败把这一行放回原样，并照旧报错。
  async function archive(topicId: string) {
    const topic = topics.value.find((row) => row.id === topicId)
    const before = topic?.status
    let forgot = false
    if (topic) {
      topicRevision += 1
      topic.status = 'archived'
      // 刚归档的正是记着的那一个：rail 那一格不该再把项目落在它身上（归档的话题还
      // 在清单里，`forgetMissingTopic` 找不到它），直接忘掉——失败再记回来。
      if (lastTopicByProject.value[topic.project_id] === topicId) {
        forgetRememberedTopic(topic.project_id)
        forgot = true
      }
    }
    try {
      const updated = await archiveTopic(topicId)
      if (topic) Object.assign(topic, updated)
      void refreshTopics()
    } catch (e) {
      if (topic) {
        topicRevision += 1
        if (before !== undefined) topic.status = before
        if (forgot) rememberTopic(topic.project_id, topic.id)
      }
      reportError(e, t('shell.workspaceErrors.archive'))
    }
  }

  async function unarchive(topicId: string) {
    const topic = topics.value.find((row) => row.id === topicId)
    const before = topic?.status
    if (topic) {
      topicRevision += 1
      topic.status = 'active'
    }
    try {
      const updated = await unarchiveTopic(topicId)
      if (topic) Object.assign(topic, updated)
      void refreshTopics()
    } catch (e) {
      if (topic && before !== undefined) {
        topicRevision += 1
        topic.status = before
      }
      reportError(e, t('shell.workspaceErrors.unarchive'))
    }
  }

  // The three ways a topic is born. Each returns the new topic so the caller can
  // navigate to it — creating a topic without opening it is never what was meant.
  // 房间是个群聊，建出来时坐着项目的默认队友；要请别的队友进来，和请人一样走
  // 成员名册。
  async function create(title: string): Promise<Topic | null> {
    const pid = projectId.value
    if (!pid) return null
    const epoch = projectEpoch
    try {
      // Untitled when nothing was typed: the backend stores its placeholder and
      // flags it (`title_source`), and every screen names it in its own language.
      const topic = await createTopic(pid, title.trim() || undefined)
      if (epoch !== projectEpoch || projectId.value !== pid) return null
      topicRevision += 1
      topics.value.unshift(topic)
      void refreshTopics()
      return topic
    } catch (e) {
      if (epoch === projectEpoch) reportError(e, t('shell.workspaceErrors.createTopic'))
      return null
    }
  }

  /** 升级出来的东西：房间里的消息变成这个房间的一张**卡**，私聊里的变成一个新
   *  房间。调用方要据此决定去哪儿——钻进那张卡，还是跳进那个房间。 */
  async function upgradeMessage(messageId: string): Promise<{ kind: 'card' | 'room'; id: string } | null> {
    try {
      const made = await upgradeBlock(messageId)
      await refreshTopics()
      // 卡带着「我挂在哪个房间」，房间没有这个问题——这就是分辨它们的那一位。
      const kind = 'room_id' in made ? 'card' : 'room'
      return { kind, id: made.id }
    } catch (e) {
      reportError(e, t('shell.workspaceErrors.convertToTopic'))
      return null
    }
  }

  return {
    projectId,
    projects,
    projectsSettled,
    accessDenied,
    openedProject,
    topics,
    members,
    agentName,
    agentHandle,
    isExternal,
    loadingTopics,
    unreadMap,
    privateUnreadMap,
    activeTopicId,
    activeDmPeer,
    chatPct,
    error,
    rootTopic,
    projectName,
    setChatPct,
    reportError,
    refreshProjects,
    refreshMembers,
    refreshTopics,
    refreshUnread,
    refreshTopicRow,
    loadPlace,
    placeById,
    isResolvingPlace,
    openProject,
    loadOpenedProject,
    unarchiveOpenProject,
    markRead,
    markDmRead,
    renameTopic,
    undoAutoTitle,
    archive,
    unarchive,
    rememberTopic,
    lastTopicIdFor,
    create,
    upgradeMessage,
  }
})
