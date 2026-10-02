// 文档那一格的取数：协同文档本身，和围着它的那几样 —— 已存的那一版、评论、节点。
//
// 和「改动」(#2130)、「预览」(#2158) 分家是同一个形状：
//   - 取数（打开协同文档、读已存的那一版、评论、节点）→ 这一层
//   - 画（编辑器和它的装饰、浮层、横条上写哪句话）
//     → components/panels/PanelDocView.vue 和它底下的 doc/DocSurface.vue，只凭 props 渲染
//
// 正文不在这一层来回搬：编辑器直接绑在协同文档上（useDocCollab），谁打的字都实时合进
// 同一份，协同服务在停手几秒后把它存回去。这一层还要读一次**已存的**那一版，因为有两
// 样东西认的是它而不是屏幕上那一份：文档 AI 的选区坐标（那是已存原文的字节位置），和
// 评论锚着的节点。存回之后房间里会收到一帧 state/doc，父层把它变成 activityTick，这一
// 层据此重读。
import type { Block, Topic } from '../cx_types'

import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { addComment, getComments, getDoc, getDocNodes, workspaceFileRawUrl } from '../api'
import { myHandle } from '../me'

import { useDocCollab } from './useDocCollab'

export interface PanelDocProps {
  topic: Topic | null
  /** 父层在 AI 动过、文档存回之后加一：已存的那一版和节点据此重读。 */
  activityTick: number
  topicList?: Topic[]
  /** 项目 AI 队友的名字。 */
  agentName?: string
}

export interface PanelDocHooks {
  /** 编辑器里现在这一版正文 markdown。没有编辑器时 null。 */
  serializeVisual: () => string | null
}

/** 「文档」这一格的全部取数：状态进、动作出，一个 DOM 都不碰。 */
export function usePanelDoc(props: PanelDocProps, hooks: PanelDocHooks) {
  const AUTHOR = myHandle()
  const projectId = computed<string | null>(() => props.topic?.project_id ?? null)
  const collab = useDocCollab(() => props.topic?.id ?? null)

  let disposed = false
  let snapshotSequence = 0
  let commentSequence = 0

  // ---- 这一篇现在是什么状态 ----
  // 自己切的只读（⋯ 里那一项）。没有编辑权限时它不起作用：那由凭证决定。
  const wantsEditable = ref(true)
  const editable = computed(() => wantsEditable.value && !collab.readOnly.value)
  const loading = computed(() => !!props.topic && !collab.synced.value && !collab.error.value)
  // 当场要说的失败（复制代码失败这一类），和文档打不开的原因，说同一个地方。
  const localError = ref<string | null>(null)
  const errorMsg = computed(() => localError.value ?? collab.error.value)

  // 已存的那一版：协同服务最近一次存回的原文和版本号。
  const rawDoc = ref('')
  const docVersion = ref(0)

  // 屏幕上这一份和已存的那一版是不是一回事。文档 AI 的选区按已存原文定位，两边不一
  // 致（还有字没存回、或者还没连上）的时候它不能提问也不能采纳。
  function matchesStored(): boolean {
    if (!collab.synced.value || collab.connection.value !== 'connected') return false
    const live = hooks.serializeVisual()
    return live !== null && live === rawDoc.value
  }

  // ---- 评论 (B4): 常驻评论区读的就是这两样 ----
  const comments = ref<Block[]>([])
  const anchorNodes = ref<Block[]>([])

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

  async function refreshComments() {
    const tid = props.topic?.id
    if (tid) await loadComments(tid).catch(() => {})
  }

  /** 重读已存的那一版（文档 AI 采纳之后也走这里）。 */
  async function reloadStored(tid: string) {
    const sequence = ++snapshotSequence
    try {
      const block = await getDoc(tid)
      if (disposed || props.topic?.id !== tid || sequence !== snapshotSequence) return
      rawDoc.value = block?.content ?? ''
      docVersion.value = block?.doc_version ?? 0
    } catch {
      // 读不到已存版本只影响文档 AI 的可用性（它会说选区无法核对），不影响编辑。
    }
    void loadComments(tid).catch(() => {})
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
      snapshotSequence++
      commentSequence++
      localError.value = null
      rawDoc.value = ''
      docVersion.value = 0
      comments.value = []
      anchorNodes.value = []
      if (id) void reloadStored(id)
    },
    { immediate: true }
  )

  // 文档存回了（state/doc）、或者芝士动过：已存的那一版和节点都可能换了。
  watch(
    () => props.activityTick,
    () => {
      const id = props.topic?.id
      if (id) void reloadStored(id)
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
    peers: collab.peers,
    readOnly: collab.readOnly,
    // 这一篇现在是什么状态
    editable,
    loading,
    errorMsg,
    rawDoc,
    docVersion,
    matchesStored,
    reloadStored,
    // 评论 / 节点（装饰的原料）
    comments,
    anchorNodes,
    liveRefIndex,
    commentMarkIndex,
    // 动作
    refreshComments,
    commentAuthor: AUTHOR,
    sendComment: (topicId: string, content: string, anchor?: string, quote?: string) =>
      addComment(topicId, content, anchor, quote),
    toggleEditable,
    setError,
    fetchDocNodes,
    imageSrc,
  }
}
