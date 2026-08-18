import type { Project, ProjectMemberRow, Topic } from '@/cx_types'

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import {
  archiveTopic,
  createTopic,
  getPrivateUnread,
  getTopic,
  getTopicUnread,
  listProjectMembers,
  listProjects,
  listTopics,
  markTopicRead,
  setTopicTitle,
  splitTopic,
  unarchiveTopic,
  upgradeBlock,
} from '@/api'
import { refreshBlockCache } from '@/lib/blockCache'
import { myHandle } from '@/me'

// 项目级状态 (P0 架构): 话题树、成员、未读、排序、栏宽——一份，供项目框架下的
// 所有页面共用。
//
// 它必须是 store 而不是 provide/inject：项目侧栏走全站的 `sidebar` 具名视图
// (App.vue)，和内容区是两个平级的组件实例，中间没有父子关系可以注入。这也正是
// 侧栏能常驻、内容区随便换页的原因。
const LAYOUT_KEY = 'cheesex.layout'

interface StoredLayout {
  railWidth?: number
  chatPct?: number
  lastProjectId?: string
}

function loadLayout(): StoredLayout {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(LAYOUT_KEY) || '{}')
    return typeof saved === 'object' && saved !== null ? (saved as StoredLayout) : {}
  } catch {
    return {} // malformed stored layout
  }
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
  const topics = ref<Topic[]>([])
  const members = ref<ProjectMemberRow[]>([])
  const loadingTopics = ref(false)

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
  const railWidth = ref(typeof stored.railWidth === 'number' ? stored.railWidth : 280)
  const chatPct = ref(typeof stored.chatPct === 'number' ? stored.chatPct : 50)
  function persistLayout() {
    localStorage.setItem(
      LAYOUT_KEY,
      JSON.stringify({
        railWidth: railWidth.value,
        chatPct: chatPct.value,
        lastProjectId: projectId.value ?? undefined,
      })
    )
  }
  function setRailWidth(w: number) {
    railWidth.value = clampNum(w, 190, 480)
    persistLayout()
  }
  function setChatPct(pct: number) {
    chatPct.value = clampNum(pct, 25, 80)
    persistLayout()
  }

  const error = ref<string | null>(null)
  function reportError(e: unknown, fallback: string) {
    error.value = e instanceof Error ? e.message : fallback
  }

  const rootTopic = computed<Topic | null>(() => topics.value.find((t) => t.kind === 'root') ?? null)
  const projectName = computed<string>(() => projects.value.find((p) => p.id === projectId.value)?.name ?? '')

  async function refreshProjects() {
    try {
      projects.value = (await listProjects()).data
    } catch (e) {
      reportError(e, '加载项目失败')
    }
  }

  async function refreshMembers() {
    const pid = projectId.value
    if (!pid) return
    try {
      const payload = await listProjectMembers(pid)
      if (projectId.value === pid) members.value = payload.data
    } catch {
      // Best-effort; the roster-driven menus just stay empty.
    }
  }

  // Silent refresh of the topic list (no spinner), so sub-topics 芝士 splits off
  // show up on their own.
  async function refreshTopics() {
    const pid = projectId.value
    if (!pid) return
    try {
      const payload = await listTopics(pid, TOPIC_SORT)
      if (projectId.value === pid) topics.value = payload.data
    } catch {
      // Best-effort background refresh; ignore.
    }
  }

  // Enter a project: everything project-scoped is reloaded, and anything left
  // over from the previous project is dropped rather than shown as this one's.
  async function openProject(id: string) {
    // Re-entering the project you were already in (back from 首页, a rail click):
    // the tree is still here and blanking it would flash the whole sidebar, but
    // it is as old as the time you spent away, so bring it up to date.
    if (projectId.value === id) {
      void refreshTopics()
      void refreshMembers()
      void refreshUnread()
      return
    }
    projectId.value = id
    persistLayout()
    topics.value = []
    members.value = []
    unreadMap.value = {}
    privateUnreadMap.value = {}
    loadingTopics.value = true
    void refreshMembers()
    if (projects.value.length === 0) void refreshProjects()
    try {
      const payload = await listTopics(id, TOPIC_SORT)
      if (projectId.value !== id) return
      topics.value = payload.data
    } catch (e) {
      reportError(e, '加载话题失败')
    } finally {
      if (projectId.value === id) loadingTopics.value = false
    }
    void refreshUnread()
  }

  async function refreshUnread() {
    const pid = projectId.value
    const me = myHandle()
    if (!pid || !me) return
    void refreshPrivateUnread(pid, me)
    try {
      const map = await getTopicUnread(pid, me)
      if (projectId.value !== pid) return
      // The open topic is being read right now — its badge never shows.
      if (activeTopicId.value) delete map[activeTopicId.value]
      // Background-refresh the timeline cache of topics whose unread grew: by
      // the time the user switches back, the reply that landed while they were
      // away is already rendered on the first frame (no late pop-in).
      for (const [tid, n] of Object.entries(map)) {
        if (n > (unreadMap.value[tid] ?? 0)) void refreshBlockCache(tid)
      }
      unreadMap.value = map
    } catch {
      // Best-effort; badges just stay as they were.
    }
  }

  async function refreshPrivateUnread(pid: string, me: string) {
    try {
      const map = await getPrivateUnread(pid, me)
      if (projectId.value !== pid) return
      // The DM being read right now never shows a badge on itself.
      if (activeDmPeer.value) delete map[activeDmPeer.value]
      privateUnreadMap.value = map
    } catch {
      // Best-effort; badges just stay as they were.
    }
  }

  // Opening a topic = reading it: bump the server-side cursor and clear the
  // badge locally (optimistic — the next refresh agrees).
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

  // After a decision the topic flips to archived — refetch it and patch the
  // list so the header chip updates.
  async function refreshTopicRow(topicId: string) {
    const pid = projectId.value
    if (!pid) return
    try {
      const fresh = await getTopic(pid, topicId)
      if (!fresh) return
      const i = topics.value.findIndex((t) => t.id === topicId)
      if (i >= 0) topics.value[i] = fresh
    } catch {
      // ignore
    }
  }

  async function renameTopic(topicId: string, title: string) {
    try {
      const updated = await setTopicTitle(topicId, title)
      const t = topics.value.find((x) => x.id === topicId)
      if (t) t.title = updated.title
    } catch (e) {
      reportError(e, '重命名失败')
    }
  }

  async function archive(topicId: string) {
    const me = myHandle()
    try {
      await archiveTopic(topicId, me)
      await refreshTopics()
    } catch (e) {
      reportError(e, '归档失败')
    }
  }

  async function unarchive(topicId: string) {
    const me = myHandle()
    try {
      await unarchiveTopic(topicId, me)
      await refreshTopics()
    } catch (e) {
      reportError(e, '取消归档失败')
    }
  }

  // The three ways a topic is born. Each returns the new topic so the caller can
  // navigate to it — creating a topic without opening it is never what was meant.
  async function create(title: string): Promise<Topic | null> {
    const pid = projectId.value
    if (!pid) return null
    try {
      // Untitled by default — the title is derived from the first message.
      const topic = await createTopic(pid, title.trim() || '新话题')
      topics.value.push(topic)
      return topic
    } catch (e) {
      reportError(e, '创建话题失败')
      return null
    }
  }

  async function split(topicId: string, title: string): Promise<Topic | null> {
    try {
      const sub = await splitTopic(topicId, title.trim() || '新话题', myHandle())
      await refreshTopics()
      return sub
    } catch (e) {
      reportError(e, '拆分子话题失败')
      return null
    }
  }

  async function upgradeMessage(messageId: string): Promise<Topic | null> {
    try {
      const topic = await upgradeBlock(messageId, myHandle())
      await refreshTopics()
      return topic
    } catch (e) {
      reportError(e, '升级为话题失败')
      return null
    }
  }

  return {
    projectId,
    projects,
    topics,
    members,
    loadingTopics,
    unreadMap,
    privateUnreadMap,
    activeTopicId,
    activeDmPeer,
    railWidth,
    chatPct,
    error,
    rootTopic,
    projectName,
    setRailWidth,
    setChatPct,
    reportError,
    refreshProjects,
    refreshMembers,
    refreshTopics,
    refreshUnread,
    refreshTopicRow,
    openProject,
    markRead,
    markDmRead,
    renameTopic,
    archive,
    unarchive,
    create,
    split,
    upgradeMessage,
  }
})
