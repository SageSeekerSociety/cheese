// 文档那一格的取数：协同文档本身，和围着它的那几样 —— 已存的那一版、评论、节点。
//
// 和「改动」(#2130)、「预览」(#2158) 分家是同一个形状：
//   - 取数（打开协同文档、读已存的那一版、评论、节点）→ 这一层
//   - 画（编辑器和它的装饰、浮层、横条上写哪句话）
//     → components/panels/PanelDocView.vue 和它底下的 doc/DocSurface.vue，只凭 props 渲染
//
// 正文不在这一层来回搬：编辑器直接绑在协同文档上（useDocCollab），谁打的字都实时合进
// 同一份，协同服务在停手几秒后把它存回去。评论锚着的是服务端那一侧的节点，存回之后房
// 间里会收到一帧 state/doc，父层把它变成 activityTick，这一层据此重读评论和节点。
import type { Block, Topic } from '../cx_types'
import type { DocEdit, DocRewriteRequest } from '../lib/docEdits'

import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { addComment, getComments, getDocNodes, workspaceFileRawUrl } from '../api'
import { applyDocEdits, getPendingSuggestions, rewriteDocSelection } from '../api/docEdits'
import { expandMentions } from '../lib/expandMentions'
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
  let commentSequence = 0

  // ---- 这一篇现在是什么状态 ----
  // 自己切的只读（⋯ 里那一项）。没有编辑权限时它不起作用：那由凭证决定。
  const wantsEditable = ref(true)
  const editable = computed(() => wantsEditable.value && !collab.readOnly.value)
  const loading = computed(() => !!props.topic && !collab.synced.value && !collab.error.value && !collab.outdated.value)
  // 当场要说的失败（复制代码失败这一类），和文档打不开的原因，说同一个地方。
  const localError = ref<string | null>(null)
  const errorMsg = computed(() => localError.value ?? collab.error.value)

  // ---- 评论 (B4): 常驻评论区读的就是这两样 ----
  const comments = ref<Block[]>([])
  const anchorNodes = ref<Block[]>([])
  // 修改建议的理由：建议本身在协同文档里，理由只在存回的那一份旁边（id → 理由）。文档
  // 里有建议时才去读（画的那一半说一声），之后每次存回跟着重读。
  const suggestionReasons = ref<Record<string, string>>({})
  let reasonSequence = 0
  let reasonsWanted = false

  const liveRefFingerprint = computed(() =>
    (props.topicList ?? []).map((t) => `${t.id}\u0000${t.title}\u0000${t.status ?? ''}`).join('\n')
  )
  // 同一次请求还要喂编辑器里的两种装饰，所以「哪一段上有徽章、哪一段上压着下划线」
  // 在这一层就算好，画的那一半照着往编辑器里投。
  const liveRefIndex = computed(() => {
    const next = new Map<number, string>()
    // 徽章上写的是一个**话题**的标题与状态，那两样在 topicList 上：支线改了标题，索引
    // 本身没变，可装饰要重建成新文案 —— 读一遍指纹，输入变过，索引就是新的一份。
    void liveRefFingerprint.value
    anchorNodes.value.forEach((n, i) => {
      // 房间里的文档节点升级出来的是一条支线；私聊里的才是房间。
      const target = n.upgraded_to_task_id || n.upgraded_to_topic_id
      if (target) next.set(i, target)
    })
    return next
  })
  // The comment underlines: node id → top-level index, then group the anchored
  // comments (with usable quotes) under their block's index.
  const commentMarkIndex = computed(() => {
    const idToIndex = new Map(anchorNodes.value.map((n, i) => [n.id, i]))
    const next = new Map<number, { id: string; quote: string }[]>()
    for (const c of comments.value) {
      const anchorId = c.reply_to
      const quote = (c.anchor_quote || '').trim()
      if (!anchorId || !quote) continue
      const idx = idToIndex.get(anchorId)
      if (idx === undefined) continue
      const list = next.get(idx) ?? []
      list.push({ id: c.id, quote })
      next.set(idx, list)
    }
    return next
  })

  // ---- 读 ----
  async function loadComments(tid: string) {
    const sequence = ++commentSequence
    const [cs, ns] = await Promise.all([getComments(tid), getDocNodes(tid)])
    if (disposed || props.topic?.id !== tid || sequence !== commentSequence) return
    comments.value = cs.data
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

  async function refreshComments() {
    const tid = props.topic?.id
    if (tid) await loadComments(tid).catch(() => {})
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

  /** 让 AI 队友改选中的字：它直接改协同文档，回执说改成了什么。 */
  function rewriteSelection(request: DocRewriteRequest) {
    const tid = props.topic?.id
    if (!tid) return Promise.reject(new Error(t('work.room.docEdit.unavailable')))
    return rewriteDocSelection(tid, request)
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
      commentSequence++
      reasonSequence++
      localError.value = null
      comments.value = []
      anchorNodes.value = []
      suggestionReasons.value = {}
      reasonsWanted = false
      if (id) void loadComments(id).catch(() => {})
    },
    { immediate: true }
  )

  // 文档存回了（state/doc）、或者芝士动过：节点都可能换了。
  watch(
    () => props.activityTick,
    () => {
      const id = props.topic?.id
      if (id) void loadComments(id).catch(() => {})
      if (id && reasonsWanted) void loadSuggestionReasons(id).catch(() => {})
    }
  )

  // 支线的标题或状态变了：重拉节点，徽章文案跟着走。侧栏每 30 秒换一批话题对象，
  // 盯的是徽章真正用到的字段，而不是数组本身。
  watch(liveRefFingerprint, () => {
    const tid = props.topic?.id
    if (tid) void loadComments(tid).catch(() => {})
  })

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
    // 评论 / 节点（装饰的原料）
    comments,
    anchorNodes,
    liveRefIndex,
    commentMarkIndex,
    suggestionReasons,
    fetchSuggestionReasons,
    // 动作
    refreshComments,
    commentAuthor: AUTHOR,
    sendComment: (topicId: string, content: string, anchor?: string, quote?: string) =>
      addComment(topicId, withMentions(content), anchor, quote),
    withMentions,
    toggleEditable,
    setError,
    fetchDocNodes,
    imageSrc,
    rewriteSelection,
    applyEdits,
  }
}
