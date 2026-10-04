// 文档那一格的取数：协同文档本身，和围着它的那几样 —— 已存的那一版、节点。评论串在
// useDocThreads。
//
// 和「改动」(#2130)、「预览」(#2158) 分家是同一个形状：
//   - 取数（打开协同文档、读已存的那一版、节点）→ 这一层
//   - 画（编辑器和它的装饰、浮层、横条上写哪句话）
//     → components/panels/PanelDocView.vue 和它底下的 doc/DocSurface.vue，只凭 props 渲染
//
// 正文不在这一层来回搬：编辑器直接绑在协同文档上（useDocCollab），谁打的字都实时合进
// 同一份，协同服务在停手几秒后把它存回去。存回之后房间里会收到一帧 state/doc，父层把它
// 变成 activityTick，这一层据此重读节点（支线徽章挂在节点上）。
import type { Block, Topic } from '../cx_types'
import type { DocAgentListener, DocAgentRequest } from '../lib/docAgent'
import type { DocEdit } from '../lib/docEdits'

import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { addComment, getDocNodes, workspaceFileRawUrl } from '../api'
import { askDocAgent, replyWithAnswer, stopDocAgent } from '../api/docAgent'
import { applyDocEdits, getPendingSuggestions } from '../api/docEdits'
import { getDocVersions, restoreDocVersion } from '../api/docHistory'
import { StreamRefused } from '../api/eventStream'
import { isAgentHandle } from '../lib/authorship'
import { dispatch } from '../lib/docAgent'
import { expandMentions } from '../lib/expandMentions'
import { refusalText } from '../lib/noticeText'
import { myHandle } from '../me'

import { useDocCollab } from './useDocCollab'

import { t } from '@/i18n'

export interface PanelDocProps {
  topic: Topic | null
  /** 父层在 AI 动过、文档存回之后加一：已存的那一版和节点据此重读。 */
  activityTick: number
  topicList?: Topic[]
  /** 项目 AI 队友的名字。 */
  agentName?: string
  /** 项目 AI 队友的 handle。 */
  agentHandle?: string | null
}

/** 「文档」这一格的全部取数：状态进、动作出，一个 DOM 都不碰。 */
export function usePanelDoc(props: PanelDocProps) {
  const AUTHOR = myHandle()
  const projectId = computed<string | null>(() => props.topic?.project_id ?? null)
  const collab = useDocCollab(() => props.topic?.id ?? null)

  let disposed = false
  let nodeSequence = 0

  // ---- 这一篇现在是什么状态 ----
  // 自己切的只读（⋯ 里那一项）。没有编辑权限时它不起作用：那由凭证决定。
  const wantsEditable = ref(true)
  const editable = computed(() => wantsEditable.value && !collab.readOnly.value)
  const loading = computed(() => !!props.topic && !collab.synced.value && !collab.error.value && !collab.outdated.value)
  // 当场要说的失败（复制代码失败这一类），和文档打不开的原因，说同一个地方。
  const localError = ref<string | null>(null)
  const errorMsg = computed(() => localError.value ?? collab.error.value)

  // ---- 节点：服务端那一侧的文档段落，支线徽章挂在上面 ----
  const anchorNodes = ref<Block[]>([])
  // 修改建议的理由：建议本身在协同文档里，理由只在存回的那一份旁边（id → 理由）。文档
  // 里有建议时才去读（画的那一半说一声），之后每次存回跟着重读。
  const suggestionReasons = ref<Record<string, string>>({})
  let reasonSequence = 0
  let reasonsWanted = false
  // 最近一次编辑（谁、什么时候）：顶栏左边那一句，跟着每一次存回重读。
  const lastEdit = ref<{ actor: string; at: string } | null>(null)
  let lastEditSequence = 0

  const liveRefFingerprint = computed(() =>
    (props.topicList ?? []).map((t) => `${t.id}\u0000${t.title}\u0000${t.status ?? ''}`).join('\n')
  )
  // 同一次请求还要喂编辑器里的两种装饰，所以「哪一段上有徽章、哪一段上压着下划线」
  // 在这一层就算好，画的那一半照着往编辑器里投。
  const liveRefIndex = computed(() => {
    const next = new Map<number, string>()
    // 徽章上写的是一个**话题**的标题与状态，那两样在 topicList 上：支线改了标题，索引
    // 本身没变，可装饰要重建成新文案 —— 读一遍指纹，输入变过，索引就是新的一份（正文
    // 那一半 watch 到这个新对象就重建装饰，标签从 topicList 现取）。这也意味着标题/状态
    // 变了**不必重拉节点**：节点树（哪一段升级成谁）只随文档本身变，不随话题表变。
    void liveRefFingerprint.value
    anchorNodes.value.forEach((n, i) => {
      // 房间里的文档节点升级出来的是一条支线；私聊里的才是房间。
      const target = n.upgraded_to_task_id || n.upgraded_to_topic_id
      if (target) next.set(i, target)
    })
    return next
  })
  // ---- 读 ----
  async function loadNodes(tid: string) {
    const sequence = ++nodeSequence
    const ns = await getDocNodes(tid)
    if (disposed || props.topic?.id !== tid || sequence !== nodeSequence) return
    anchorNodes.value = ns.data
  }

  async function loadSuggestionReasons(tid: string) {
    const sequence = ++reasonSequence
    const pending = await getPendingSuggestions(tid)
    if (disposed || props.topic?.id !== tid || sequence !== reasonSequence) return
    suggestionReasons.value = Object.fromEntries(pending.filter((s) => s.reason).map((s) => [s.id, s.reason!]))
  }

  /** 文档里出现了修改建议：读它们的理由。 */
  function fetchSuggestionReasons() {
    const tid = props.topic?.id
    reasonsWanted = true
    if (tid) void loadSuggestionReasons(tid).catch(() => {})
  }

  /** 评论里写的「@名字」换成点名（和对话框发消息一样）：AI 队友只认点名，写成字它收不到。 */
  function withMentions(content: string): string {
    const handle = props.agentHandle
    return handle ? expandMentions(content, [{ handle, label: props.agentName }]) : content
  }

  /** 当场要说的失败（目前只有复制代码失败）；null = 把它关掉。 */
  function setError(message: string | null) {
    localError.value = message
  }

  function toggleEditable() {
    if (collab.readOnly.value) return
    wantsEditable.value = !wantsEditable.value
  }

  /** 文档的节点树：正文里那些段落在服务端那一侧的身份（id、出自哪一次活）。 */
  function fetchDocNodes(): Promise<Block[]> {
    const tid = props.topic?.id
    if (!tid) return Promise.resolve([])
    return getDocNodes(tid).then((r) => r.data)
  }

  /** 在文档里找 AI 队友：它在自己的会话里答，答的过程一条条交给 `listener`。 */
  async function askAgent(request: DocAgentRequest, listener: DocAgentListener): Promise<void> {
    const tid = props.topic?.id
    if (!tid) throw new Error(t('work.room.docEdit.unavailable'))
    try {
      await askDocAgent(tid, request, (event, data) => dispatch(listener, event, data))
    } catch (error) {
      if (error instanceof StreamRefused) throw new Error(refusalText(error.body, error.message))
      throw error
    }
  }

  function stopAgent(conversation: string) {
    const tid = props.topic?.id
    return tid ? stopDocAgent(tid, conversation) : Promise.resolve()
  }

  /** 把这一问的回答放进一条新评论：评论写着问了什么，回答是 AI 队友在下面的回复。回执是
   *  这条评论的 id，标记由拿着编辑器的那一层放上去。 */
  async function answerToComment(conversation: string, quote: string, question: string): Promise<string> {
    const tid = props.topic?.id
    if (!tid) throw new Error(t('work.room.comments.unavailable'))
    const posted = await addComment(tid, question, quote)
    await replyWithAnswer(tid, conversation, posted.id)
    return posted.id
  }

  /** 以自己的名义替换正文里的字：撤销、还原 AI 队友的修改都走这里。 */
  function applyEdits(edits: DocEdit[]) {
    const tid = props.topic?.id
    if (!tid) return Promise.reject(new Error(t('work.room.docEdit.unavailable')))
    return applyDocEdits(tid, edits)
  }

  // Display-time image src resolution: workspace-relative paths (uploads/x.png)
  // render through the raw-file API; absolute http(s)/data URLs pass through.
  // The node attr keeps the ORIGINAL path, so the Markdown never gets a URL.
  function imageSrc(src: string): string {
    if (/^(https?:|data:|blob:|\/)/i.test(src)) return src
    const pid = projectId.value
    if (!pid) return src
    return workspaceFileRawUrl(pid, src.replace(/^\.\//, ''), props.topic?.id)
  }

  watch(
    () => props.topic?.id ?? null,
    (id) => {
      nodeSequence++
      reasonSequence++
      localError.value = null
      anchorNodes.value = []
      suggestionReasons.value = {}
      reasonsWanted = false
      lastEdit.value = null
      lastEditSequence++
      if (id) void loadNodes(id).catch(() => {})
      if (id) void loadLastEdit(id).catch(() => {})
    },
    { immediate: true }
  )

  async function loadLastEdit(tid: string) {
    const sequence = ++lastEditSequence
    const page = await getDocVersions(tid, { limit: 1 })
    if (disposed || props.topic?.id !== tid || sequence !== lastEditSequence) return
    const top = page.versions[0]
    lastEdit.value = top ? { actor: top.actor, at: top.created_at } : null
  }

  /** handle 读成名字：自己是「你」，AI 队友是它的名字，在线的人用他们的名字。 */
  function nameOf(handle: string): string {
    if (handle === AUTHOR) return t('work.room.doc.you')
    if (handle === props.agentHandle || isAgentHandle(handle)) return props.agentName ?? handle
    return collab.peers.value.find((peer) => peer.handle === handle)?.name ?? handle
  }

  /** 修改记录的一页，新的在前。 */
  function loadVersions(before?: number) {
    const tid = props.topic?.id
    if (!tid) return Promise.reject(new Error(t('work.room.docEdit.unavailable')))
    return getDocVersions(tid, { before })
  }

  /** 恢复到某一版：在最新一版上再记一版。 */
  function restoreVersion(version: number, expected: number) {
    const tid = props.topic?.id
    if (!tid) return Promise.reject(new Error(t('work.room.docEdit.unavailable')))
    return restoreDocVersion(tid, version, expected)
  }

  // 文档存回了（state/doc）、或者芝士动过：节点都可能换了。
  watch(
    () => props.activityTick,
    () => {
      const id = props.topic?.id
      if (id) void loadNodes(id).catch(() => {})
      if (id) void loadLastEdit(id).catch(() => {})
      if (id && reasonsWanted) void loadSuggestionReasons(id).catch(() => {})
    }
  )

  // 支线的标题或状态变了，徽章要跟着改字 —— 这一步由 liveRefIndex 出新的那份索引、
  // 正文那一半据此重建装饰来完成（见上面的 liveRefIndex）。不在这里再拉一次节点：侧
  // 栏每 30 秒换一批话题对象、每半分钟真正的变更也就几条，而节点树并不随话题表变，
  // 重拉只会拿回同一份数据（实测每次切话题多一条 /docs）。

  onBeforeUnmount(() => {
    disposed = true
  })

  return {
    // 协同文档
    session: collab.session,
    connection: collab.connection,
    outdated: collab.outdated,
    peers: collab.peers,
    readOnly: collab.readOnly,
    // 这一篇现在是什么状态
    editable,
    loading,
    errorMsg,
    // 节点（徽章的原料）
    liveRefIndex,
    suggestionReasons,
    fetchSuggestionReasons,
    // 动作
    commentAuthor: AUTHOR,
    sendComment: (topicId: string, content: string, quote: string) => addComment(topicId, withMentions(content), quote),
    askAgent,
    stopAgent,
    answerToComment,
    lastEdit: computed(() => (lastEdit.value ? { name: nameOf(lastEdit.value.actor), at: lastEdit.value.at } : null)),
    nameOf,
    loadVersions,
    restoreVersion,
    withMentions,
    toggleEditable,
    setError,
    fetchDocNodes,
    imageSrc,
    applyEdits,
  }
}
