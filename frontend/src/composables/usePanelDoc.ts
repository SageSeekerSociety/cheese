// 文档那一格的取数：房间的文档是哪一份，协同文档本身，和围着它的那几样 —— 已存的
// 那一版、节点。评论串在 useDocThreads。
//
// 和「改动」(#2130)、「预览」(#2158) 分家是同一个形状：
//   - 取数（打开协同文档、读已存的那一版、节点）→ 这一层
//   - 画（编辑器和它的装饰、浮层、横条上写哪句话）
//     → components/panels/PanelDocView.vue 和它底下的 doc/DocSurface.vue，只凭 props 渲染
//
// 正文不在这一层来回搬：编辑器直接绑在协同文档上（useDocCollab），谁打的字都实时合进
// 同一份，协同服务在停手几秒后把它存回去。存回之后协同连接上会收到一帧（useDocCollab
// 的 stores），这一层据此重读最近一次编辑和修改建议的理由。
import type { Block, OverviewAutoBlock, Topic } from '../cx_types'
import type { DocAgentListener, DocAgentRequest } from '../lib/docAgent'
import type { DocEdit } from '../lib/docEdits'

import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { addComment, getDocNodes, getOverviewAuto, workspaceFileRawUrl } from '../api'
import { askDocAgent, replyWithAnswer, stopDocAgent } from '../api/docAgent'
import { getRoomDocument } from '../api/docCollab'
import { applyDocEdits, getPendingSuggestions } from '../api/docEdits'
import { getDocVersions, restoreDocVersion } from '../api/docHistory'
import { StreamRefused } from '../api/eventStream'
import { getDocumentAbout, renameDocument } from '../api/projectDocuments'
import { isAgentHandle } from '../lib/authorship'
import { dispatch } from '../lib/docAgent'
import { expandMentions } from '../lib/expandMentions'
import { refusalText } from '../lib/noticeText'
import { myHandle } from '../me'

import { useDocCollab } from './useDocCollab'

import { t } from '@/i18n'

/** 不在哪个对话里的一份文档（项目资料库里的）：直接给编号，不经对话去问。 */
export interface PanelDocument {
  id: string
  projectId: string
  title: string
}

export interface PanelDocProps {
  topic: Topic | null
  /** 打开的是这个房间里某个任务的实况文档，而不是房间自己的。 */
  taskId?: string | null
  /** 有它就打开这一份，`topic` 不再决定是哪份文档。 */
  document?: PanelDocument | null
  /** 父层在 AI 动过之后加一：已存的那一版据此重读。 */
  activityTick: number
  /** 项目 AI 队友的名字。 */
  agentName?: string
  /** 项目 AI 队友的 handle。 */
  agentHandle?: string | null
  /** 画在一整页里（资料库的那份章程）：总览的其余两块不在那儿画，也就不去取。 */
  bare?: boolean
}

/** 「文档」这一格的取数原样递给面板（props）：面板自己不认识接口。 */
export type PanelDocBundle = ReturnType<typeof usePanelDoc>

/** 「文档」这一格的全部取数：状态进、动作出，一个 DOM 都不碰。 */
export function usePanelDoc(props: PanelDocProps) {
  const AUTHOR = myHandle()
  const projectId = computed<string | null>(() => props.document?.projectId ?? props.topic?.project_id ?? null)
  // 房间的文档是哪一份：切到一个房间时问一次。之后的读写都对着这份文档的 id。
  const documentId = ref<string | null>(null)
  const resolveError = ref<string | null>(null)
  const collab = useDocCollab(() => documentId.value)

  let disposed = false
  let documentSequence = 0

  // ---- 这一篇现在是什么状态 ----
  // 自己切的只读（⋯ 里那一项）。没有编辑权限时它不起作用：那由凭证决定。
  const wantsEditable = ref(true)
  const editable = computed(() => wantsEditable.value && !collab.readOnly.value)
  const loading = computed(
    () =>
      (!!props.topic || !!props.document) &&
      !collab.synced.value &&
      !collab.error.value &&
      !resolveError.value &&
      !collab.outdated.value &&
      !collab.deleted.value
  )
  // 当场要说的失败（复制代码失败这一类），和文档打不开的原因，说同一个地方。
  const localError = ref<string | null>(null)
  const errorMsg = computed(() => localError.value ?? resolveError.value ?? collab.error.value)

  // 修改建议的理由：建议本身在协同文档里，理由只在存回的那一份旁边（id → 理由）。文档
  // 里有建议时才去读（画的那一半说一声），之后每次存回跟着重读。
  const suggestionReasons = ref<Record<string, string>>({})
  let reasonSequence = 0
  let reasonsWanted = false
  // 最近一次编辑（谁、什么时候）：顶栏左边那一句，跟着每一次存回重读。
  const lastEdit = ref<{ actor: string; at: string } | null>(null)
  let lastEditSequence = 0

  // ---- 读 ----
  async function loadSuggestionReasons(did: string) {
    const sequence = ++reasonSequence
    const pending = await getPendingSuggestions(did)
    if (disposed || documentId.value !== did || sequence !== reasonSequence) return
    suggestionReasons.value = Object.fromEntries(pending.filter((s) => s.reason).map((s) => [s.id, s.reason!]))
  }

  /** 文档里出现了修改建议：读它们的理由。 */
  function fetchSuggestionReasons() {
    const did = documentId.value
    reasonsWanted = true
    if (did) void loadSuggestionReasons(did).catch(() => {})
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
    const did = documentId.value
    if (!did) return Promise.resolve([])
    return getDocNodes(did).then((r) => r.data)
  }

  /** 在文档里找 AI 队友：它在自己的会话里答，答的过程一条条交给 `listener`。 */
  async function askAgent(request: DocAgentRequest, listener: DocAgentListener): Promise<void> {
    const did = documentId.value
    if (!did) throw new Error(t('work.room.docEdit.unavailable'))
    try {
      await askDocAgent(did, request, (event, data) => dispatch(listener, event, data))
    } catch (error) {
      if (error instanceof StreamRefused) throw new Error(refusalText(error.body, error.message))
      throw error
    }
  }

  function stopAgent(conversation: string) {
    const did = documentId.value
    return did ? stopDocAgent(did, conversation) : Promise.resolve()
  }

  /** 把这一问的回答放进一条新评论：评论写着问了什么，回答是 AI 队友在下面的回复。回执是
   *  这条评论的 id，标记由拿着编辑器的那一层放上去。 */
  async function answerToComment(conversation: string, quote: string, question: string): Promise<string> {
    const did = documentId.value
    if (!did) throw new Error(t('work.room.comments.unavailable'))
    const posted = await addComment(did, question, quote)
    await replyWithAnswer(did, conversation, posted.id)
    return posted.id
  }

  /** 以自己的名义替换正文里的字：撤销、还原 AI 队友的修改都走这里。 */
  function applyEdits(edits: DocEdit[]) {
    const did = documentId.value
    if (!did) return Promise.reject(new Error(t('work.room.docEdit.unavailable')))
    return applyDocEdits(did, edits)
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

  async function resolveDocument(tid: string, taskId: string | null) {
    const sequence = ++documentSequence
    try {
      // A task's document is its own conversation's.
      const id = (await getRoomDocument(taskId ?? tid)).id
      if (disposed || props.topic?.id !== tid || sequence !== documentSequence) return
      documentId.value = id
    } catch (cause) {
      if (disposed || sequence !== documentSequence) return
      resolveError.value = cause instanceof Error ? cause.message : String(cause)
    }
  }

  watch(
    () => [props.document?.id ?? null, props.topic?.id ?? null, props.taskId ?? null] as const,
    ([given, topicId, taskId]) => {
      documentSequence++
      resolveError.value = null
      if (given) {
        documentId.value = given
        return
      }
      documentId.value = null
      if (topicId) void resolveDocument(topicId, taskId)
    },
    { immediate: true }
  )

  watch(documentId, (id) => {
    reasonSequence++
    localError.value = null
    suggestionReasons.value = {}
    reasonsWanted = false
    lastEdit.value = null
    lastEditSequence++
    if (id) void loadLastEdit(id).catch(() => {})
  })

  async function loadLastEdit(did: string) {
    const sequence = ++lastEditSequence
    const page = await getDocVersions(did, { limit: 1 })
    if (disposed || documentId.value !== did || sequence !== lastEditSequence) return
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
    const did = documentId.value
    if (!did) return Promise.reject(new Error(t('work.room.docEdit.unavailable')))
    return getDocVersions(did, { before })
  }

  /** 恢复到某一版：在最新一版上再记一版。 */
  function restoreVersion(version: number, expected: number) {
    const did = documentId.value
    if (!did) return Promise.reject(new Error(t('work.room.docEdit.unavailable')))
    return restoreDocVersion(did, version, expected)
  }

  // 文档存回了，或者芝士动过：最近一次编辑和修改建议的理由都可能换了。
  watch([() => props.activityTick, collab.stores], () => {
    const id = documentId.value
    if (id) void loadLastEdit(id).catch(() => {})
    if (id && reasonsWanted) void loadSuggestionReasons(id).catch(() => {})
  })

  // ---- 总览的其余两块（#1889 ②③） ----
  // 平台从话题和结论现拼的那两块，跟在正文下面。只有根话题的文档是「整个项目」那一份，
  // 别的房间的文档写的是它自己，所以只有它去取；整页画的那一份（资料库的章程）不画这两
  // 块，也不去取。
  const overviewBlocks = ref<OverviewAutoBlock[]>([])
  const overviewFailed = ref(false)
  let overviewSequence = 0

  async function loadOverview(): Promise<void> {
    const tid = props.topic?.kind === 'root' && !props.bare ? props.topic.id : null
    const sequence = ++overviewSequence
    if (!tid) {
      overviewBlocks.value = []
      overviewFailed.value = false
      return
    }
    try {
      const page = await getOverviewAuto(tid)
      if (disposed || sequence !== overviewSequence) return
      overviewBlocks.value = page.blocks ?? []
      overviewFailed.value = false
    } catch {
      if (disposed || sequence !== overviewSequence) return
      overviewBlocks.value = []
      overviewFailed.value = true
    }
  }
  watch(
    () => [props.topic?.kind === 'root' && !props.bare ? props.topic?.id ?? null : null, props.activityTick] as const,
    () => void loadOverview(),
    { immediate: true }
  )

  onBeforeUnmount(() => {
    disposed = true
  })

  return {
    // 协同文档
    session: collab.session,
    connection: collab.connection,
    outdated: collab.outdated,
    deleted: collab.deleted,
    renames: collab.renames,
    peers: collab.peers,
    readOnly: collab.readOnly,
    // 这一篇现在是什么状态
    editable,
    loading,
    errorMsg,
    // 这份文档
    documentId,
    suggestionReasons,
    fetchSuggestionReasons,
    // 总览的其余两块
    overviewBlocks,
    overviewFailed,
    loadOverview,
    // 动作
    commentAuthor: AUTHOR,
    sendComment: (content: string, quote: string) => {
      const did = documentId.value
      if (!did) return Promise.reject(new Error(t('work.room.comments.unavailable')))
      return addComment(did, withMentions(content), quote)
    },
    askAgent,
    stopAgent,
    answerToComment,
    lastEdit: computed(() => (lastEdit.value ? { name: nameOf(lastEdit.value.actor), at: lastEdit.value.at } : null)),
    nameOf,
    loadVersions,
    restoreVersion,
    withMentions,
    /** 资料库文档改名；回执是存下来的名字。 */
    rename: async (title: string) => {
      const did = documentId.value
      if (!did) throw new Error(t('work.room.docEdit.unavailable'))
      return (await renameDocument(did, title)).title ?? ''
    },
    /** 资料库文档现在叫什么（别人改了名之后重读）。 */
    currentTitle: async () => {
      const did = documentId.value
      return did ? (await getDocumentAbout(did)).title ?? '' : null
    },
    toggleEditable,
    setError,
    fetchDocNodes,
    imageSrc,
    applyEdits,
  }
}
