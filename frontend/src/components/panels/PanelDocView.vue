<script setup lang="ts">
// 文档那一格的**画**：顶栏、正文、共用的工具侧栏。
//
// 它只凭 props 渲染，不认识接口也不认识路由 —— 打开协同文档、评论、节点都在
// composables/usePanelDoc.ts 里（#2143）。正文编辑器本身在 doc/DocSurface.vue：这里画出
// 它的位置，把取来的东西递下去，把底下发上来的动作接住。正文不经过这里：编辑器直接绑
// 在协同文档（`session`）上，没有一个「保存」要这一层去管。
import type { DocBacklink } from '../../api/docLinks'
import type { DocConnection, DocPeer, DocSession } from '../../composables/useDocCollab'
import type { SendDocComment } from '../../composables/useDocCommentDraft'
import type { useMentionOutside } from '../../composables/useMentionOutside'
import type { PanelDocument } from '../../composables/usePanelDoc'
import type { MentionPoolEntry } from '../../composables/useRoomMentionPicker'
import type { Block, Topic } from '../../cx_types'
import type { DocAgentListener, DocAgentRequest } from '../../lib/docAgent'
import type { DocAuthor } from '../../lib/docAuthors'
import type { CommentSpot } from '../../lib/docCommentSpots'
import type { DocEdit } from '../../lib/docEdits'
import type { DocVersionPage } from '../../lib/docHistory'
import type { LinkableDocument } from '../../lib/docMentionMenu'
import type { DocReviewRequest } from '../../lib/docReview'
import type { DocThreadActions, DocThreadState, ThreadPlace } from '../../lib/docThreadTypes'
import type { CanvasData } from '../../types/canvas'

import { computed, nextTick, onActivated, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { toast } from 'vuetify-sonner'

import { scrollBehavior } from '@/utils/motion'

import { useDocAgent } from '../../composables/useDocAgent'
import { useDocFind } from '../../composables/useDocFind'
import { useDocOutline } from '../../composables/useDocOutline'
import { useDocReview } from '../../composables/useDocReview'
import { useDocSuggestions } from '../../composables/useDocSuggestions'
import { isAgentHandle } from '../../lib/authorship'
import { anchorComment } from '../../lib/docCommentSpots'
import { exportMarkdown } from '../../lib/docSchema'
import { draftWaiting, takeDraft } from '../../lib/overviewDraft'
import { topicTitle } from '../../lib/topicState'

import { documentPrintHtml } from './doc/blocks/print'
import DocBacklinks from './doc/DocBacklinks.vue'
import DocCommentPanel from './doc/DocCommentPanel.vue'
import DocEditLayer from './doc/DocEditLayer.vue'
import DocFindBar from './doc/DocFindBar.vue'
import DocHistory from './doc/DocHistory.vue'
import DocReviewStrip from './doc/DocReviewStrip.vue'
import DocSuggestionStrip from './doc/DocSuggestionStrip.vue'
import DocSurface from './doc/DocSurface.vue'
import DocTopBar from './doc/DocTopBar.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import MentionOutsidePrompt from '@/components/chat/MentionOutsidePrompt.vue'
import DocAuthors from '@/components/library/DocAuthors.vue'
import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    topic: Topic | null
    /** 项目资料库里的一份文档：大标题是它自己的，在页上就能改。 */
    document?: PanelDocument | null
    /** 打开着的时候被人删了。 */
    deleted?: boolean
    /** 父层在 AI 动过之后加一：总览那一块据此重读。 */
    activityTick: number
    /** 项目 AI 队友的名字。 */
    agentName?: string
    /** 项目 AI 队友的 handle：评论里点它的名写成它的名字。 */
    agentHandle?: string | null
    /** 账号 → 名字：正文和评论里的 `<@账号>` 标签上写的字。 */
    mentionNames?: Record<string, string>
    /** 正文里打 @ 时列出来的人。 */
    mentionPeople?: MentionPoolEntry[]
    /** 这份文档 @ 到并通知过的人（只有他们的 `<@handle>` 画成 @chip）。 */
    notifiedMentions?: string[]
    /** 正文里（或某个旧版本里）有了 `<@handle>` 时调一下：读这份文档通知过谁。 */
    fetchNotifiedMentions?: () => void
    /** 项目话题表：正文里的 `<#id>` chip 靠它认名字。 */
    topicList?: Topic[]
    /** 打 # 时能引用的文档。 */
    documentList?: LinkableDocument[]
    /** 引用了这份文档的地方，画在正文末尾。 */
    backlinks?: DocBacklink[]
    /** 画在一整页里（项目文档的项目目标）：页头已经说了这是什么，不再画大标题和总览自动区。 */
    bare?: boolean
    /** 文档的作者（`lib/docAuthors.ts`）：画在大标题下面，并列的头像和名字。没有就不画。 */
    authors?: DocAuthor[]
    /** 任务的实况文档：界面上不给它固定标题，正文自己说。 */
    untitled?: boolean
    /** 顶栏画到页面上的这个位置（CSS 选择器），和页面自己的那一行并成一行。 */
    barTo?: string
    /** 跟着外面那一列一起滚：正文有多长就多高，顶栏吸在这一篇的顶上。 */
    flow?: boolean
    // ---- 这一篇现在是什么状态 ----
    /** 这一篇的协同文档；还没打开时是 null。 */
    session: DocSession | null
    /** 能不能改：没有编辑权限，或者自己切到了只读。 */
    editable: boolean
    /** 没有编辑权限（凭证说的），切不回编辑。 */
    readOnly: boolean
    /** 只能提修改建议（导师）：能打字，打的都记成修订；不接受、不拒绝、不改标题。 */
    suggestOnly?: boolean
    /** 打的字记成修订。 */
    suggesting?: boolean
    setSuggesting?: (on: boolean) => void
    /** 正文还在路上。 */
    loading: boolean
    connection: DocConnection
    /** 协同服务已换了新版的文档格式，这一页得刷新才能再打开文档。 */
    outdated?: boolean
    /** 同一篇文档打开着的其他人。 */
    peers: DocPeer[]
    errorMsg: string | null
    // ---- 评论区 ----
    commentAuthor?: string
    threadState: DocThreadState
    threadActions: DocThreadActions
    /** 发一条评论（评的是 `quote` 那几个字）；回执里有它的 id，标记由这一层放到字上。 */
    sendComment?: (content: string, quote: string) => Promise<{ id: string }>
    /** 重读评论串。 */
    refreshThreads?: () => Promise<void>
    /** 我刚写的评论 @ 了打不开这份文档的人：评论栏顶上问一句。 */
    commentOutside?: ReturnType<typeof useMentionOutside>
    // ---- 装饰的原料（原样递给正文那一半） ----
    /** 修改建议的理由（建议 id → 理由），卡上写出来。 */
    suggestionReasons?: Record<string, string>
    /** 文档里有了新的修改建议时调一下：读它们的理由。 */
    fetchSuggestionReasons?: () => void
    /** 取一份最新的节点树（闪某一段要它）。 */
    fetchDocNodes: () => Promise<Block[]>
    /** 文档里引用的一张图拿什么画。 */
    imageSrc: (src: string) => Promise<string>
    /** 导出 PDF 时那张图放进纸里的地址：自带字节的 data: URL。取不到时给空串。 */
    imageData?: (src: string) => Promise<string>
    /** 把排好的一页交给平台印成 PDF 并下载；没接这一条时「⋯」里不给「导出 PDF」。 */
    savePdf?: (html: string, filename: string) => Promise<void>
    /** 文档里的画布拿什么读写。 */
    canvas?: CanvasData | null
    // ---- 动作 ----
    toggleEditable: () => void
    setError: (message: string | null) => void
    /** 在文档里找 AI 队友：问一次，答的过程交给 `listener`；没有时不给这个入口。 */
    askAgent?: (request: DocAgentRequest, listener: DocAgentListener) => Promise<void>
    /** 停下这一问。 */
    stopAgent?: (conversation: string) => Promise<unknown>
    /** 把这一问的回答放进一条新评论；回执是那条评论的 id。 */
    answerToComment?: (conversation: string, quote: string, question: string) => Promise<string>
    /** 以自己的名义替换正文里的字（撤销、还原 AI 队友的修改）。 */
    applyDocEdits?: (edits: DocEdit[]) => Promise<unknown>
    /** 撤销、恢复 AI 队友一次回答对这篇的修改（`/assistant/changes`）。 */
    undoAgentChanges?: (changes: number[], redo: boolean) => Promise<unknown>
    // ---- 修改记录 ----
    /** 最近一次编辑：谁（读成名字）、什么时候。 */
    lastEdit?: { name: string; at: string } | null
    /** handle 读成名字。 */
    nameOf?: (handle: string) => string
    /** handle 读成他挑过的头像地址；他没挑过、或不在名册上时给空串，画首字母。 */
    avatarOf?: (handle: string) => string
    loadVersions?: (before?: number) => Promise<DocVersionPage>
    restoreVersion?: (version: number, expected: number) => Promise<unknown>
    // ---- 我在文档里 @ 了打不开它的人 ----
    /** 要不要把他们加进频道：名字已经连成一串；没有就是 null。 */
    mentionOutside?: { names: string; membersOnly: boolean; mayAdd: boolean; adding: boolean } | null
  }>(),
  {
    document: null,
    deleted: false,
    suggestOnly: false,
    suggesting: false,
    setSuggesting: undefined,
    agentName: () => t('work.room.defaultAgentName'),
    agentHandle: null,
    mentionNames: undefined,
    mentionPeople: () => [],
    notifiedMentions: () => [],
    fetchNotifiedMentions: undefined,
    topicList: () => [],
    documentList: () => [],
    backlinks: () => [],
    bare: false,
    authors: () => [],
    untitled: false,
    barTo: undefined,
    flow: false,
    outdated: false,
    commentAuthor: '',
    sendComment: undefined,
    refreshThreads: undefined,
    commentOutside: undefined,
    askAgent: undefined,
    stopAgent: undefined,
    answerToComment: undefined,
    suggestionReasons: () => ({}),
    fetchSuggestionReasons: undefined,
    applyDocEdits: undefined,
    undoAgentChanges: undefined,
    lastEdit: null,
    nameOf: (handle: string) => handle,
    avatarOf: () => '',
    loadVersions: undefined,
    restoreVersion: undefined,
    mentionOutside: null,
    canvas: null,
    imageData: undefined,
    savePdf: undefined,
  }
)

const emit = defineEmits<{
  'open-topic': [topicId: string, blockId?: string]
  'mention-click': [handle: string]
  'open-file': [path: string]
  /** 资料库文档的标题改成了这样。 */
  rename: [title: string]
  delete: []
  /** 「移动到…」（#3774）。 */
  move: []
  share: []
  'mention-outside-add': []
  'mention-outside-dismiss': []
}>()

// 顶栏画到页面上的那个位置（`barTo`）。这个位置在页面自己那一行里，要等页面挂进文档
// 里才找得到；而项目目标那一页是保活的：人走开时它整棵树摘在文档外面，文档要是这时候
// 才打开，按选择器去找什么也找不到。Teleport 拿着一个找不到的目标照样挂上，顶栏就哪儿
// 都没画，之后它的每一次更新、卸下都踩在一棵没挂上的子树上，页面跟着坏掉。所以位置由
// 这里自己找：挂上之后、页面回到屏幕上时各找一次，找到了才把顶栏送过去。
const barTarget = shallowRef<HTMLElement | null>(null)
function findBarTarget() {
  barTarget.value = props.barTo ? document.querySelector<HTMLElement>(props.barTo) : null
}
onMounted(findBarTarget)
onActivated(findBarTarget)
watch(() => props.barTo, findBarTarget, { flush: 'post' })
// 页面不再要顶栏画过去时（`barTo` 没了），当场就画回原处，不等上面那次重找。
const barEl = computed(() => (props.barTo ? barTarget.value : null))

/** 大标题：对话的名字，或者资料库文档自己的名字（没起名时是「未命名文档」）。 */
const docTitle = computed(() =>
  props.document ? props.document.title || t('work.room.doc.untitled') : props.topic ? topicTitle(props.topic) : ''
)

// 资料库文档的标题框里正在打的字。框里的字属于打字的人：页面在这时重画（编辑器连上、
// 能不能改变了）不能把它换回文档原来的名字；没人在框里时，才跟着文档的名字走。
const titleDraft = ref(props.document?.title ?? '')
const titleEditing = ref(false)
watch(
  () => [props.document?.id, props.document?.title] as const,
  ([id, name], [beforeId, beforeName]) => {
    // 外层每次重画都递来一个新的 document 对象：编号和名字都没变就不是「文档改名了」。
    if (id === beforeId && name === beforeName) return
    if (id !== beforeId || !titleEditing.value) titleDraft.value = name ?? ''
  }
)
function commitTitle() {
  titleEditing.value = false
  emit('rename', titleDraft.value.trim())
}

// 评论区自己是一个组件：列表、折叠、写评论的输入框都在里面。这一层只负责把它开出来 ——
// 抛上去的那两件事（锚点 + 引文）它自己接，因为 ref 就在这一层。
const commentsRef = ref<InstanceType<typeof DocCommentPanel> | null>(null)
const openId = ref<string | null>(null)
const bodyRef = ref<HTMLElement | null>(null)
let pulseTimer: ReturnType<typeof setTimeout> | undefined

// B1 Phase 2 (cross-view link, panel-level): when a chat action that changed the
// doc is clicked, flash the document + scroll it into view — connecting the
// process (conversation) to the state (doc). The paragraph-level flash itself
// belongs to the surface (it is the one holding the editor); this is the
// whole-page fallback it calls when it cannot locate the paragraphs.
const pulsing = ref(false)

async function pulse() {
  if (props.flow) bodyRef.value?.scrollIntoView({ block: 'start', behavior: scrollBehavior() })
  else bodyRef.value?.scrollTo({ top: 0, behavior: scrollBehavior() })
  if (pulseTimer) clearTimeout(pulseTimer)
  pulsing.value = false
  await nextTick()
  pulsing.value = true
  pulseTimer = setTimeout(() => {
    pulsing.value = false
  }, 1200)
}
onBeforeUnmount(() => {
  if (pulseTimer) clearTimeout(pulseTimer)
})

// 正文区的滚动：代码块工具条贴在 <pre> 上，正文一滚它就指错地方了。往上发一次
// 「滚了」，由拿着编辑器的正文那一半收起来。
const scrollTick = ref(0)
function reload() {
  window.location.reload()
}

function onBodyScroll() {
  scrollTick.value++
}
// 跟着外面一起滚时，滚的是外面那一列：听它。
let flowScroller: HTMLElement | null = null
function scrollParent(el: HTMLElement | null): HTMLElement | null {
  for (let at = el?.parentElement ?? null; at; at = at.parentElement) {
    const y = getComputedStyle(at).overflowY
    if (y === 'auto' || y === 'scroll') return at
  }
  return null
}
watch(
  () => [props.flow, bodyRef.value] as const,
  ([flow, body]) => {
    flowScroller?.removeEventListener('scroll', onBodyScroll)
    flowScroller = flow ? scrollParent(body) : null
    flowScroller?.addEventListener('scroll', onBodyScroll, { passive: true })
  },
  { flush: 'post' }
)
onBeforeUnmount(() => flowScroller?.removeEventListener('scroll', onBodyScroll))

const surfaceRef = ref<InstanceType<typeof DocSurface> | null>(null)
function highlightTurn(turnId: string) {
  void surfaceRef.value?.highlightTurn(turnId)
}
function openComment(spot: CommentSpot) {
  commentsRef.value?.open(spot)
}
/** 发出一条评论，再把它的标记放到评的那几个字上。 */
const postComment: SendDocComment = async (_topicId, content, spot) => {
  if (!props.sendComment) throw new Error(t('work.room.comments.unavailable'))
  const posted = await props.sendComment(content, spot.quote)
  const ed = surfaceRef.value?.editor
  if (ed && spot.quote) anchorComment(ed, spot, posted.id)
  openId.value = posted.id
  await props.refreshThreads?.()
}
// 评论：没解决的那些在正文里标出来，正在看的那一串标得重些。
const openThreads = computed(
  () =>
    new Set(props.threadState.threads.filter((thread) => thread.state === 'open').map((thread) => thread.comment.id))
)
function placeOfThread(id: string): ThreadPlace {
  return surfaceRef.value?.threadPlace(id) ?? null
}
function revealThread(id: string) {
  surfaceRef.value?.revealThread(id)
}

// 大纲与文档内查找：都只读正文那一半的编辑器（正文编辑器住在 DocSurface 里），这一
// 层拿着它算出标题、匹配，并把点击 / 上下跳落成滚动。逻辑本身在各自的 composable 与
// lib 里，这里只有接线。
const editorOf = () => surfaceRef.value?.editor ?? null
const { headings: outlineHeadings, go: goHeading } = useDocOutline(editorOf)
const {
  query: findQuery,
  open: findOpen,
  total: findTotal,
  current: findCurrent,
  setQuery: setFindQuery,
  step: stepFind,
  setOpen: setFindOpen,
  toggle: toggleFind,
} = useDocFind(editorOf)

// 点名（`<@handle>`）读成名字：外面给了名册就用名册，没给至少认得 AI 队友。
const mentionNames = computed<Record<string, string>>(
  () => props.mentionNames ?? (props.agentHandle ? { [props.agentHandle]: props.agentName } : {})
)

/** 能决定正文：能改，且不只是提修改建议（导师只提建议，接受、拒绝、改标题、恢复旧版都不归他）。 */
const decides = computed(() => props.editable && !props.suggestOnly)

const agentOptions = {
  editor: () => surfaceRef.value?.editor ?? null,
  ask: () => props.askAgent,
  stop: () => props.stopAgent,
  editable: () => decides.value,
  undoChanges: () => props.undoAgentChanges,
  toComment: () => props.answerToComment,
  agentName: () => props.agentName,
  onError: (message: string) => props.setError(message),
}
// 选中一段找 AI 队友（浮条上那一个），和对整篇找它（顶栏上那一个）。
const rewrite = useDocAgent({ ...agentOptions, scope: 'selection' })
const docAgent = useDocAgent({ ...agentOptions, scope: 'document' })
const canAskDocument = computed(() => !!props.agentHandle && !!props.askAgent && !props.suggestOnly)
// 总览页上点了「让 AI 队友起草」，跳到了这一篇：编辑器在了、能问、能改，就由对整篇的
// 那个 AI 队友接着起草（`lib/overviewDraft`）。
watch(
  [() => props.document?.id, () => surfaceRef.value?.editor, canAskDocument, () => props.editable],
  ([id, editor, canAsk, editable]) => {
    if (!id || !editor || !canAsk || !editable || !draftWaiting(id)) return
    const note = takeDraft(id)
    if (note !== null) docAgent.draft(note)
  },
  { immediate: true }
)
const suggestions = useDocSuggestions(() => surfaceRef.value?.editor ?? null)
/** 人的 handle 读成名字：自己是「你」、在线的人是他们的名字，都读不出来时用名册。 */
function personName(handle: string): string {
  const resolved = props.nameOf(handle)
  return resolved === handle ? mentionNames.value[handle] ?? handle : resolved
}
/** 建议条上的名字：全出自一个人时写那个人的名字，混着几个人时不写名字。 */
function suggestionAuthorName(authors: string[]): string | null {
  const unique = [...new Set(authors.filter(Boolean))]
  if (unique.length !== 1) return null
  const handle = unique[0]
  if (handle === props.agentHandle || isAgentHandle(handle)) return props.agentName
  return personName(handle)
}
const pendingSuggestionAuthor = computed(() => suggestionAuthorName(suggestions.list.value.map((s) => s.author)))
const decidedSuggestionAuthor = computed(() => suggestionAuthorName(suggestions.decided.value.authors))
// 建议那一条：点顶栏上的「N 处建议」打开，从第一处看起。
const suggestionsOpen = ref(false)
function toggleSuggestions() {
  suggestionsOpen.value = !suggestionsOpen.value
  if (suggestionsOpen.value) suggestions.step(1)
}
function dismissSuggestions() {
  suggestions.dismissDone()
  suggestionsOpen.value = false
}
const historyOpen = ref(false)
/** 导出 Markdown：文档现在的样子，待处理的建议不算在内。 */
function exportDoc() {
  if (!props.session) return
  const blob = new Blob([exportMarkdown(props.session.doc)], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `${docTitle.value || 'document'}.md`
  link.click()
  URL.revokeObjectURL(url)
  // 文件悄悄落进下载文件夹，不说一句就像没导出。
  toast(t('work.room.doc.exportedMarkdown'))
}
/** 文档名能当文件名：每个系统都不收的那几个字符换成空格。 */
function fileName(title: string): string {
  return Array.from(title, (ch) => ('\\/:*?"<>|'.includes(ch) ? ' ' : ch)).join('')
}
/** 导出 PDF：同一份正文，排成一页纸交给平台的打印浏览器。菜单在点下去时已经关了，
 *  所以失败要说出来，不然这一下就成了没反应。 */
async function exportPdf() {
  if (!props.session || !props.savePdf) return
  try {
    const html = await documentPrintHtml({
      title: docTitle.value || 'document',
      markdown: exportMarkdown(props.session.doc),
      names: { mentionNames: props.mentionNames ?? {}, topicTitles: {} },
      image: props.imageData,
    })
    await props.savePdf(html, `${fileName(docTitle.value || 'document')}.pdf`)
    toast(t('work.room.doc.exportedPdf'))
  } catch (cause) {
    props.setError(cause instanceof Error && cause.message ? cause.message : t('work.room.doc.exportPdfFailed'))
  }
}
watch(
  () => suggestions.list.value.map((s) => s.id).join(' '),
  (ids, before) => {
    if (ids && ids.split(' ').some((id) => !before?.split(' ').includes(id))) props.fetchSuggestionReasons?.()
  }
)
// 正文里第一次出现 `<@handle>`（打开时就有，或后来有人写进来），或者翻开修改记录：
// 读一次这份文档通知过谁，之后由取数那一层跟着存回重读。没有 @ 的文档不去问。
let mentionsAsked = false
watch(
  () => props.session,
  () => (mentionsAsked = false)
)
function askMentions() {
  if (mentionsAsked) return
  mentionsAsked = true
  props.fetchNotifiedMentions?.()
}
watch(
  () => surfaceRef.value?.editor,
  (editor, _old, onCleanup) => {
    if (!editor) return
    const look = () => {
      if (!mentionsAsked && editor.state.doc.textContent.includes('<@')) askMentions()
    }
    look()
    editor.on('update', look)
    onCleanup(() => editor.off('update', look))
  }
)
watch(historyOpen, (open) => open && askMentions())
const review = useDocReview({
  editor: () => surfaceRef.value?.editor ?? null,
  applyEdits: () => props.applyDocEdits,
  onError: (message) => props.setError(message),
})
/** 「查看改动」：在正文里一处处标出某个人让 AI 队友改的那几处。 */
function reviewEdits(request: DocReviewRequest) {
  rewrite.close()
  review.open(request)
}
function locateComment(commentId: string) {
  commentsRef.value?.locate(commentId)
}
watch([() => props.topic?.id, () => props.document?.id, () => props.commentAuthor], () => {
  openId.value = null
  rewrite.close()
  review.close()
  docAgent.close()
  suggestionsOpen.value = false
  historyOpen.value = false
  setFindOpen(false)
})

// 编辑器里现在这一版正文（开发时的探针读它），只有这里知道编辑器在哪。
defineExpose({
  pulse,
  highlightTurn,
  reviewEdits,
  serializeVisual: () => surfaceRef.value?.serializeVisual() ?? null,
})
</script>

<template>
  <!-- 根元素上不要放 Vuetify 的 display 工具类：WorkPanel 用 v-show 切 tab，而
       `.d-flex` 是 display: flex !important，会盖掉 v-show 写进去的 inline
       display: none（见 src/vShowDisplayUtilities.spec.ts）。 -->
  <div class="doc" :class="{ 'doc--flow': flow }">
    <div v-if="!topic && !document" class="flex-grow-1 d-flex align-center justify-center text-medium-emphasis">
      <div class="text-center">
        <v-icon size="48" class="mb-2 text-disabled">mdi-file-document-outline</v-icon>
        <div>{{ t('work.room.doc.pickTopic') }}</div>
      </div>
    </div>

    <div v-else-if="deleted" class="flex-grow-1 d-flex align-center justify-center text-medium-emphasis">
      <div class="t-body">{{ t('work.room.doc.deleted') }}</div>
    </div>

    <div v-else-if="outdated" class="flex-grow-1 d-flex align-center justify-center">
      <div class="text-center">
        <div class="t-body mb-3">{{ t('work.room.doc.outdated') }}</div>
        <BaseButton kind="secondary" size="sm" @click="reload">{{ t('work.room.doc.reload') }}</BaseButton>
      </div>
    </div>

    <template v-else>
      <!-- Stage: the editor + (optionally) a docked tool panel beside it. -->
      <div class="doc-stage flex-grow-1">
        <Teleport v-if="!barTo || barEl" :to="barEl || 'body'" :disabled="!barEl">
          <DocTopBar
            class="doc-top"
            :class="{ 'doc-top--inline': !!barTo }"
            :loading="loading"
            :connection="connection"
            :peers="peers"
            :editable="editable"
            :read-only="readOnly"
            :last-edit="lastEdit"
            :suggestion-count="suggestions.list.value.length"
            :suggestions-open="suggestionsOpen"
            :comment-count="openThreads.size"
            :comments-open="commentsRef?.opened ?? false"
            :agent-name="agentName"
            :agent-handle="agentHandle"
            :agent="canAskDocument ? docAgent : undefined"
            :mention-names="mentionNames"
            :headings="outlineHeadings"
            :find-open="findOpen"
            :deletable="!!document && !readOnly && !suggestOnly"
            :pdfable="!!savePdf"
            :suggest-only="suggestOnly"
            :suggesting="suggesting"
            @delete="emit('delete')"
            @move="emit('move')"
            @share="emit('share')"
            @toggle-suggestions="toggleSuggestions"
            @toggle-comments="commentsRef?.toggle()"
            @toggle-editable="toggleEditable"
            @set-suggesting="(on: boolean) => setSuggesting?.(on)"
            @history="historyOpen = true"
            @export="exportDoc"
            @export-pdf="exportPdf"
            @open-thread="locateComment"
            @toggle-find="toggleFind"
            @outline-select="goHeading"
          />
        </Teleport>
        <DocFindBar
          :open="findOpen"
          :query="findQuery"
          :total="findTotal"
          :current="findCurrent"
          @update:query="setFindQuery"
          @next="stepFind(1)"
          @prev="stepFind(-1)"
          @close="setFindOpen(false)"
        />
        <Transition name="doc-menu">
          <DocReviewStrip
            v-if="review.request.value"
            :agent-name="agentName"
            :count="review.live.value.length"
            @step="review.step"
            @close="review.close"
          />
        </Transition>
        <Transition name="doc-menu">
          <DocSuggestionStrip
            v-if="suggestionsOpen"
            :author="pendingSuggestionAuthor"
            :decided-author="decidedSuggestionAuthor"
            :count="suggestions.list.value.length"
            :decided="suggestions.decided.value"
            :editable="decides"
            @step="suggestions.step"
            @accept-all="suggestions.decideAll(true)"
            @reject-all="suggestions.decideAll(false)"
            @dismiss="dismissSuggestions"
          />
        </Transition>
        <MentionOutsidePrompt
          v-if="mentionOutside"
          class="doc-mention-outside"
          in-document
          :names="mentionOutside.names"
          :members-only="mentionOutside.membersOnly"
          :may-add="mentionOutside.mayAdd"
          :adding="mentionOutside.adding"
          @add="emit('mention-outside-add')"
          @dismiss="emit('mention-outside-dismiss')"
        />
        <!-- Editor surface — a Feishu Docs page: white, padded, centered column. -->
        <!-- 顶栏下面、正文上面的那一格：任务把芝士这一轮的清单放在这里。 -->
        <slot name="lead" />
        <DocCommentPanel
          ref="commentsRef"
          v-model:open-id="openId"
          :topic-id="topic?.id ?? null"
          :author="commentAuthor"
          :send-comment="postComment"
          :thread-state="threadState"
          :thread-actions="threadActions"
          :place-of="placeOfThread"
          :agent-name="agentName"
          :mention-names="mentionNames"
          :mention-people="mentionPeople"
          :outside="commentOutside"
          :name-of="nameOf"
          :avatar-of="avatarOf"
          :writable="!readOnly"
          @locate="revealThread"
        >
          <div
            ref="bodyRef"
            class="doc-body"
            :class="{ 'overflow-y-auto': !flow, readonly: !editable }"
            @scroll.passive="onBodyScroll"
          >
            <div class="doc-page" :class="{ 'doc-pulse': pulsing }">
              <!-- Large document title (Feishu Docs): the topic's, or the library document's own. -->
              <input
                v-if="document && !bare"
                v-model="titleDraft"
                class="doc-page__title doc-page__title--input"
                autocomplete="off"
                :placeholder="t('work.room.doc.titlePlaceholder')"
                :aria-label="t('work.room.doc.titleLabel')"
                :readonly="!decides"
                maxlength="200"
                @focus="titleEditing = true"
                @keydown.enter.prevent="($event.target as HTMLInputElement).blur()"
                @blur="commitTitle"
              />
              <h1 v-else-if="!bare && !untitled" class="doc-page__title">{{ docTitle }}</h1>
              <DocAuthors v-if="!bare && authors.length" class="doc-page__authors t-meta c-muted" :authors="authors" />
              <!-- 正文本身。 -->
              <DocSurface
                ref="surfaceRef"
                :editable="editable"
                :suggesting="suggesting"
                :author="commentAuthor"
                :loading="loading"
                :session="session"
                :title="docTitle"
                :placeholder="document ? t('work.room.doc.emptyPlaceholderLibrary') : ''"
                :topic-id="topic?.id ?? null"
                :topic-list="topicList"
                :document-list="documentList"
                :document-id="document?.id ?? null"
                :mention-names="mentionNames"
                :mention-people="mentionPeople"
                :notified-mentions="notifiedMentions"
                :can-comment="!readOnly"
                :open-threads="openThreads"
                :active-thread="openId"
                :fetch-doc-nodes="fetchDocNodes"
                :image-src="imageSrc"
                :canvas="canvas"
                :pulse="pulse"
                :scroll-tick="scrollTick"
                :agent-name="agentName"
                :agent-handle="agentHandle"
                @open-topic="emit('open-topic', $event)"
                @mention-click="emit('mention-click', $event)"
                @open-file="emit('open-file', $event)"
                @open-comment="openComment"
                @agent="(spot: CommentSpot, request?: string) => rewrite.open(spot, request)"
                @locate-comment="locateComment"
                @error="setError"
              />

              <DocEditLayer
                :editor="surfaceRef?.editor ?? null"
                :agent-name="agentName"
                :agent-handle="agentHandle"
                :name-of="nameOf"
                :avatar-of="avatarOf"
                :editable="decides"
                :rewrite="rewrite"
                :review="review"
                :suggestions="suggestions"
                :suggestion-reasons="suggestionReasons"
                :mention-names="mentionNames"
                @open-thread="locateComment"
              />
              <DocBacklinks
                :items="backlinks"
                :names="agentHandle ? { ...mentionNames, [agentHandle]: agentName } : mentionNames"
                @open="(id: string, block?: string) => emit('open-topic', id, block)"
              />
            </div>
          </div>
        </DocCommentPanel>
      </div>
      <!-- /.doc-stage -->

      <DocHistory
        v-if="loadVersions"
        v-model:open="historyOpen"
        :load="loadVersions"
        :restore="restoreVersion"
        :editable="decides"
        :name-of="nameOf"
        :mention-names="mentionNames"
        :notified-mentions="notifiedMentions"
      />

      <!-- Floating, never clipped: the old flow-layout alert sat below the
           scroll stage and rendered half-hidden at the panel edge. -->
      <v-alert
        v-if="errorMsg"
        type="error"
        density="compact"
        class="doc-error-toast"
        closable
        @click:close="setError(null)"
      >
        {{ errorMsg }}
      </v-alert>
    </template>
  </div>
</template>

<style scoped>
.doc-top {
  flex: 0 0 auto;
  border-bottom: 0.5px solid var(--line);
  background: var(--surface);
}
/* 并进页面那一行时，下边线和底色是那一行的，整条靠右。 */
.doc-top--inline {
  flex: 0 1 auto;
  min-width: 0;
  padding: 0;
  border-bottom: none;
  background: transparent;
}
.doc-top--inline :deep(.doc-top-bar__state) {
  flex: 0 1 auto;
}
.doc {
  /* In the split workspace the doc is a full white surface that fills the pane —
     not a floating card on a gray canvas (which left gray gutters around it). */
  background: var(--surface);
  container-type: inline-size;
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
}
.doc-body {
  padding: 8px 0 80px;
  position: relative;
  display: flex;
  justify-content: center;
}
/* The document column: white surface (inherits .doc), text capped for
   readability and centered. No card border/radius — it IS the surface. */
.doc-page {
  position: relative;
  width: 100%;
  max-width: 48rem;
  background: transparent;
  padding: 16px 24px 72px;
}
@container (min-width: 48rem) {
  .doc-page {
    padding: 24px 44px 72px;
  }
}
/* 能悬停的设备上，行首的手柄（DocOverlays .doc-handle，42px）待在左边距里，不被裁掉。 */
@media (hover: hover) {
  .doc-page {
    padding-left: 48px;
  }
}
/* The topic title, above the document. */
.doc-page__title {
  max-width: 720px;
  margin: 0 auto 24px;
  font-family: var(--font-display);
  font-size: 23px;
  font-weight: 600;
  line-height: var(--lh-23);
  letter-spacing: -0.02em;
  color: var(--ink);
}
/* 作者一行贴在大标题下面，和标题同宽同起点；正文的间距照旧留在它下面。 */
.doc-page__authors {
  display: flex;
  max-width: 720px;
  margin: -16px auto 24px;
}

/* A library document's title is typed in place. */
.doc-page__title--input {
  display: block;
  width: 100%;
  padding: 0;
  border: 0;
  outline: none;
  background: transparent;
}
.doc-page__title--input::placeholder {
  color: var(--faint);
}

/* B1 Phase 2: a brief highlight when a chat action points at the doc. */
.doc-pulse {
  animation: docPulse 1.2s var(--ease-out);
}
/* 不动的时候：整页框一下，直到脚本收回（pulse）。 */
@media (prefers-reduced-motion: reduce) {
  .doc-pulse {
    animation: none;
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--ok) 45%, transparent);
  }
}
@keyframes docPulse {
  0% {
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--ok) 45%, transparent);
    background: var(--ok-wash);
  }
  100% {
    box-shadow: 0 0 0 0 transparent;
    background: transparent;
  }
}
/* Stage holds the editor and, when pinned, the docked tool panel beside it. */
/* 跟着外面那一列一起滚：没有自己的高度和滚动条，顶栏吸在这一篇的顶上，滚过这一篇
   就跟着走。按内容排高、不让外面那一列压扁：压扁后正文被评论栏外框剪掉，整列也就
   没东西可滚了。 */
.doc--flow {
  flex: 0 0 auto;
  height: auto;
}
.doc--flow .doc-stage {
  overflow: visible;
}
.doc--flow .doc-top {
  position: sticky;
  top: 0;
  z-index: var(--z-raised);
}
.doc--flow .doc-body {
  padding-bottom: 16px;
}
.doc-stage {
  position: relative;
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}
.doc-body {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
}
.md-content :deep(p) {
  margin: 0 0 6px;
}
.md-content :deep(p:last-child) {
  margin-bottom: 0;
}
.md-content :deep(code) {
  font-family: var(--font-mono);
  background: var(--fill);
  padding: 0.5px 5px;
  border-radius: var(--radius-sm);
  font-size: 0.88em;
}
.md-content :deep(pre) {
  background: var(--fill);
  padding: 10px 12px;
  border-radius: var(--radius-md);
  overflow-x: auto;
}

/* 贴在正文上方，和横条之间留一行的空。 */
.doc-mention-outside {
  margin-top: 8px;
}
.doc-error-toast {
  position: absolute;
  left: 50%;
  bottom: 18px;
  transform: translateX(-50%);
  z-index: var(--z-shell);
  max-width: min(560px, calc(100% - 32px));
  overflow-wrap: anywhere;
  box-shadow: var(--shadow-2);
}
</style>
