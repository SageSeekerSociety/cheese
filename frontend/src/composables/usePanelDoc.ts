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
import type { Block, Topic } from '../cx_types'
import type { DocAgentListener, DocAgentRequest } from '../lib/docAgent'
import type { AuthoredDocument } from '../lib/docAuthors'
import type { DocEdit } from '../lib/docEdits'
import type { MentionMember } from '../lib/expandMentions'
import type { CanvasData } from '../types/canvas'

import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { addComment, getDocNodes } from '../api'
import { AgentChangesApi } from '../api/agentChanges'
import { changeCanvas, copyCanvas, createCanvas, getCanvas, setCanvasHeight } from '../api/canvases'
import { askDocAgent, replyWithAnswer, stopDocAgent } from '../api/docAgent'
import { getRoomDocument } from '../api/docCollab'
import { applyDocEdits, getNotifiedMentions, getPendingSuggestions } from '../api/docEdits'
import { getDocVersions, restoreDocVersion } from '../api/docHistory'
import { StreamRefused } from '../api/eventStream'
import {
  documentImageDataUrl,
  documentImageUrl,
  documentPdf,
  getDocumentAbout,
  renameDocument,
} from '../api/projectDocuments'
import { isAgentHandle } from '../lib/authorship'
import { dispatch } from '../lib/docAgent'
import { expandMentions } from '../lib/expandMentions'
import { refusalText } from '../lib/noticeText'
import { myHandle } from '../me'

import { useDocCollab } from './useDocCollab'
import { useDocLinks } from './useDocLinks'
import { useMentionOutside } from './useMentionOutside'

import { t } from '@/i18n'
import { queryClient } from '@/query/client'

/** 不在哪个对话里的一份文档（项目资料库里的）：直接给编号，不经对话去问。 */
export interface PanelDocument extends AuthoredDocument {
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
  /** 画在一整页里（项目文档页的项目目标）：总览的其余两块不在那儿画，也就不去取。 */
  bare?: boolean
}

/** 「文档」这一格的取数原样递给面板（props）：面板自己不认识接口。 */
export type PanelDocBundle = ReturnType<typeof usePanelDoc>

/** 「文档」这一格的全部取数：状态进、动作出，一个 DOM 都不碰。
 *
 *  `mentionPool` 是评论发送前把「@名字」认成人用的那份候选（备注名、原名、handle）；
 *  由接线外壳给，因为那份名册只有它读得到（`usePanelDocWiring`）。不给时只认 AI 队友，
 *  和加这一条之前一样。 */
export function usePanelDoc(props: PanelDocProps, mentionPool?: () => MentionMember[]) {
  const AUTHOR = myHandle()
  // 房间的文档是哪一份：切到一个房间时问一次。之后的读写都对着这份文档的 id。
  const documentId = ref<string | null>(null)
  const resolveError = ref<string | null>(null)
  const collab = useDocCollab(() => documentId.value)
  // 我存回的一版 @ 了打不开这份文档的人：问我要不要把他们加进频道（和发消息一样）。名字
  // 由画的那一层按名册读，这里只记 handle。
  const mentionOutside = useMentionOutside({ place: () => documentId.value, nameOf: (handle) => handle })
  watch(collab.mentionedOutside, (outside) => {
    if (outside) mentionOutside.offer({ mentioned_outside: outside })
  })

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
  // 正文 @ 到并通知过谁：只有他们的名字画成 @chip。刚存的那一版可能刚通知了一个人，
  // 所以跟着每一次存回重读。
  // 正文里有了 @ 才去读（画的那一半说一声），之后每次存回跟着重读。
  const notifiedMentions = ref<string[]>([])
  let notifiedSequence = 0
  let notifiedWanted = false
  async function loadNotifiedMentions(did: string) {
    const sequence = ++notifiedSequence
    const told = await getNotifiedMentions(did)
    if (disposed || documentId.value !== did || sequence !== notifiedSequence) return
    notifiedMentions.value = told
  }

  /** 正文里出现了 @：读这份文档通知过谁。 */
  function fetchNotifiedMentions() {
    const did = documentId.value
    notifiedWanted = true
    if (did) void loadNotifiedMentions(did).catch(() => {})
  }

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

  /** 评论里写的「@名字」换成点名（和对话框发消息一样）：AI 队友只认点名，写成字它收不到；
      人也一样 —— 备注名不是名册上的名字，后端认不出来，写成字没人收到通知。

      `broadcast: false`：评论文档没有「所有人」可通知，候选菜单也不给这两个词
      （`useCommentMentions`）。开着的话手打一个 `@all` 会变成谁都不通知的 `<@all>`，
      正文里那个 @ 还不见了。 */
  function withMentions(content: string): string {
    const handle = props.agentHandle
    const pool: MentionMember[] = [...(mentionPool?.() ?? [])]
    if (handle) pool.unshift({ handle, label: props.agentName })
    return pool.length ? expandMentions(content, pool, [], { broadcast: false }) : content
  }

  /** 当场要说的失败（目前只有复制代码失败）；null = 把它关掉。 */
  function setError(message: string | null) {
    localError.value = message
  }

  function toggleEditable() {
    if (collab.readOnly.value) return
    wantsEditable.value = !wantsEditable.value
  }

  // 建议模式（修订）：自己切的；只能提建议的人（导师）一直在里面。
  const wantsSuggesting = ref(false)
  const suggesting = computed(() => collab.suggestOnly.value || wantsSuggesting.value)
  function setSuggesting(on: boolean) {
    wantsSuggesting.value = on
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

  /** 撤销（或恢复）AI 队友这一次回答对这篇文档的修改：服务端以自己的名义把那几处换回去。
   *  一次回答对一篇文档的几处改动是一起撤销的，给第一处就够了。 */
  function undoAgentChanges(changes: number[], redo: boolean) {
    if (!changes.length) return Promise.resolve()
    return redo ? AgentChangesApi.redo(changes[0]) : AgentChangesApi.undo(changes[0])
  }

  // 文档里引用的一张图拿什么画：按这份文档去取（读得了文档的人才看得到），路径落在
  // 哪儿由后端按文档是谁的来定。本来就是地址的（http(s)、data、blob、站内绝对路径）
  // 原样画。节点里留着原来的路径，Markdown 里不会出现取回来的地址。
  function imageSrc(src: string): Promise<string> {
    if (/^(https?:|data:|blob:|\/)/i.test(src)) return Promise.resolve(src)
    const did = documentId.value
    if (!did) return Promise.reject(new Error(t('work.room.docEdit.unavailable')))
    return documentImageUrl(did, src.replace(/^\.\//, ''))
  }

  // 印成纸时那张图：同一个取法，但把字节带进纸里（打印的浏览器没有凭证，也够不着
  // 这份文档）。本来就是自足的地址（公开网址、data:）照原样给。
  function imageData(src: string): Promise<string> {
    if (/^(https?:|data:)/i.test(src)) return Promise.resolve(src)
    const did = documentId.value
    if (!did) return Promise.reject(new Error(t('work.room.docEdit.unavailable')))
    return documentImageDataUrl(did, src.replace(/^\.\//, ''))
  }

  /** 把排好的一页交给平台印成 PDF，回执落到浏览器的下载里。 */
  async function savePdf(html: string, filename: string): Promise<void> {
    const did = documentId.value
    if (!did) throw new Error(t('work.room.doc.exportPdfFailed'))
    const blob = await documentPdf(did, html)
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.append(link)
    link.click()
    link.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  // 文档里的画布：读写都按画布自己的编号；新建、复制一块要知道是给哪份文档的。
  function thisDocument(): string {
    const did = documentId.value
    if (!did) throw new Error(t('work.room.docEdit.unavailable'))
    return did
  }
  const canvas: CanvasData = {
    load: getCanvas,
    change: changeCanvas,
    setHeight: setCanvasHeight,
    create: async () => (await createCanvas(thisDocument())).id,
    copy: async (source) => (await copyCanvas(source, thisDocument())).id,
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

  // 「现在打开的是哪一份」：三个 id 拼成一串字面量，watch 比的是这串字本身，值没变就不
  // 触发下面的回调。不能直接返回数组字面量 —— 每次求值都是新数组，Vue 按引用比较，值没变
  // 也判定为变化：任务页的数据一刷新，props.topic 就换成另一个对象（id 没变），回调跑起来
  // 先把 documentId 置空，协同连接跟着拆掉、正文销毁，等同一个 id 解析回来再挂一遍。
  function openKey(): string {
    return JSON.stringify([props.document?.id ?? null, props.topic?.id ?? null, props.taskId ?? null])
  }

  watch(
    openKey,
    () => {
      documentSequence++
      resolveError.value = null
      const given = props.document?.id ?? null
      if (given) {
        documentId.value = given
        return
      }
      documentId.value = null
      const topicId = props.topic?.id ?? null
      if (topicId) void resolveDocument(topicId, props.taskId ?? null)
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
    notifiedMentions.value = []
    notifiedSequence++
    notifiedWanted = false
    if (id) void loadLastEdit(id).catch(() => {})
  })

  // 同一页历史同时被要两次（几处一起重读），后来的跟着在飞的那一条走；之后的每一次
  // 照常现问（staleTime 0）。
  function readVersions(did: string, page: { before?: number; limit?: number }) {
    return queryClient.fetchQuery({
      queryKey: ['document', did, 'history', page],
      queryFn: () => getDocVersions(did, page),
      staleTime: 0,
    })
  }

  async function loadLastEdit(did: string) {
    const sequence = ++lastEditSequence
    const page = await readVersions(did, { limit: 1 })
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
    return readVersions(did, { before })
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
    if (id && notifiedWanted) void loadNotifiedMentions(id).catch(() => {})
    if (id && reasonsWanted) void loadSuggestionReasons(id).catch(() => {})
  })

  const links = useDocLinks({
    projectId: () => props.document?.projectId ?? props.topic?.project_id ?? null,
    documentId: () => documentId.value,
  })

  onBeforeUnmount(() => {
    disposed = true
  })

  return {
    // 文档之间的链接：打 # 能引用的，和引用了它的地方
    linkable: links.linkable,
    backlinks: links.backlinks,
    // 协同文档
    session: collab.session,
    connection: collab.connection,
    outdated: collab.outdated,
    deleted: collab.deleted,
    renames: collab.renames,
    peers: collab.peers,
    readOnly: collab.readOnly,
    suggestOnly: collab.suggestOnly,
    suggesting,
    // 这一篇现在是什么状态
    editable,
    loading,
    errorMsg,
    // 这份文档
    documentId,
    /** 我 @ 了打不开这份文档的人：要不要加进频道。 */
    mentionOutside,
    suggestionReasons,
    fetchSuggestionReasons,
    notifiedMentions,
    fetchNotifiedMentions,
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
      if (!did) return null
      const about = await queryClient.fetchQuery({
        queryKey: ['document', did, 'about'],
        queryFn: () => getDocumentAbout(did),
        staleTime: 0,
      })
      return about.title ?? ''
    },
    toggleEditable,
    setSuggesting,
    setError,
    fetchDocNodes,
    imageSrc,
    imageData,
    savePdf,
    canvas,
    applyEdits,
    undoAgentChanges,
  }
}
