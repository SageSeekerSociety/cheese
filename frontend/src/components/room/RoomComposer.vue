<script setup lang="ts">
// 输入区：打字、@ 补全、贴/拖/挑文件、以及「交给芝士」。
//
// **叫不叫芝士，只由这条消息本身决定**，这里没有一个自己存着状态的开关。开关能和
// 正文说不一样的话（亮着、正文里却没有 @），那时候「这条到底算不算叫了它」谁也答不
// 上来。三个入口（手打 @、点按钮、⌘/Ctrl+Enter）写的都是同一个 @，你看得见，也能
// 自己删。这条最后交给谁由后端定（@、回复、任务负责人那几条规矩），输入框顶上那一行
// 和那颗头像画的都是后端对这份草稿的回答（`useAddressing`），不在这里另算一遍。
//
// 草稿、回复对象和待发附件**不归它管**：那几样要跟着话题走、跨刷新活下来，而话题
// 什么时候换只有房间知道。正文是 v-model，其余由房间传进来——它只负责画和发。
//
// 这一层现在只做三件事：把这几块拼在一起、处理键盘（回车到底是挑人、换行还是
// 发送）、以及把拖进来的东西交给房间（#2143）。@ 补全的状态机在
// `composables/useRoomMentionPicker.ts`，菜单、待发条和动作行各自是一件只管画的
// 东西（`MentionMenu.vue` / `ComposerChipRow.vue` / `ComposerActions.vue`）。
import type { RowRefs } from '@/composables/useChatComposer'
import type { MentionPoolEntry } from '@/composables/useRoomMentionPicker'
import type { VoiceHeard } from '@/composables/useVoiceInput'
import type { Addressing } from '../../api/messages'
import type { ChatAttachment, Topic } from '../../cx_types'
import type { LinkableDocument } from '../../lib/docMentionMenu'
import type { QuotedContext } from '../../lib/quotedContext'
import type { RefNames } from '../../lib/refChip'

import { computed, inject, nextTick, ref, watch } from 'vue'
import { useDisplay } from 'vuetify'

import { useAddressing } from '@/composables/useAddressing'
import { useComposerText } from '@/composables/useComposerText'
import { useComposerVoice } from '@/composables/useComposerVoice'
import { vGuideAnchor } from '@/composables/useGuideAnchors'
import { useRoomMentionPicker } from '@/composables/useRoomMentionPicker'

import { pastedTextName } from '../../lib/attachments'
import { expandComposerMentions, mentionsAgent as containsAgentMention } from '../../lib/composerMentions'
import { searchLibraryFiles } from '../../lib/libraryApi'
import { linkableDocuments } from '../../lib/linkableDocuments'
import { PENDING_NOTES, sendableCount as countSendable } from '../../lib/pendingNotes'
import { myHandle } from '../../me'

import ComposerActions from './ComposerActions.vue'
import ComposerChecklistDialog from './ComposerChecklistDialog.vue'
import ComposerChipRow from './ComposerChipRow.vue'
import ComposerEditor from './ComposerEditor.vue'
import ComposerRecipient from './ComposerRecipient.vue'
import ComposerSummon from './ComposerSummon.vue'
import ComposerViewing from './ComposerViewing.vue'
import ComposerVoiceBar from './ComposerVoiceBar.vue'
import MentionMenu from './MentionMenu.vue'
import PendingNotesTray from './PendingNotesTray.vue'
import ReminderDialog from './ReminderDialog.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import i18n, { t } from '@/i18n'
import { toast } from '@/lib/toast'

const props = defineProps<{
  topic: Topic | null
  /**
   * 项目本体那一栏的输入框：界面导览指的是它（登记 `composer-input` /
   * `composer-summon`）。支线、任务里的输入框不登记，否则后挂上的那只会把名字抢走，
   * 导览就圈着旁边那栏的输入框。
   */
  guided?: boolean
  /** @ 得到的人：这个房间里的，加上项目里还没进这个房间的。 */
  mentionPool: MentionPoolEntry[]
  /** @ 得到的话题，用来把「@话题名」展开成 <#id>。 */
  topicList: Topic[]
  /** 正文里的引用画成标签时写的名字（和时间线上同一份）。 */
  refs: RowRefs
  /** 这个房间交给的那位 AI 队友。名册还没到时是 null，两个召唤入口都关着。 */
  agentSeat: { handle: string; label: string } | null
  agentName: string
  /** 和芝士私聊：每条都是说给它听的，没有「交给它」这颗按钮。 */
  alwaysSummon: boolean
  /** 和 AI 队友私聊时为 false：只有它一位，⌘/Ctrl+Enter 没有可交的人，就是发送。 */
  handOff?: boolean
  /** 输入框那一行提示语。 */
  hint: string
  /** 已经传上去、等着跟下一条一起发出去的附件。 */
  atts: ChatAttachment[]
  attsUploading: boolean
  /** 这条消息回复的是哪一条，读出来的样子（「回复 谁：说了什么」）。不回复时是 null。 */
  replyLabel?: string | null
  /** 这条消息带着的引用（幻灯片的一页、文档的一段……）。没有时是 null。 */
  quote?: QuotedContext | null
  /** 发一张自己的清单；不给就没有这个入口。 */
  postChecklist?: (steps: string[]) => Promise<boolean>
  /** 把说的一段交给后端听写；不给就没有麦克风。 */
  hearVoice?: (wav: Blob) => Promise<VoiceHeard>
  /** 正在这个频道里跑一轮的 AI 队友（handle）。 */
  busyAgents?: string[]
  /** 我在这个频道里上一次交给的那位队友：头像平时画它，点一下就交给它。 */
  lastMate?: string | null
  /** 这条发在哪段对话里（房间，或房间里的任务、支线）。不给就不问「交给谁」，那一行空着。 */
  place?: string | null
  /** 这条回复的那条消息的 id。 */
  replyTo?: string | null
  /** 这段对话自己的状态（任务开着还是关了）：变了，「交给谁」那一行重问一次。 */
  placeState?: string | null
}>()

const checklistOpen = ref(false)

const emit = defineEmits<{
  /** 发这一条。附件由房间补上——它才知道此刻待发条里有什么。 */
  /** `expectsAgent`：发的人看到的是「交给」某位队友（送到时已经不交给了，后端退回来）。 */
  (e: 'send', message: { content: string; summon: boolean; expectsAgent: boolean }): void
  (e: 'files', files: File[]): void
  (e: 'drop-files', event: DragEvent): void
  (e: 'paste', event: ClipboardEvent): void
  (e: 'remove-att', index: number): void
  /** 上传失败的那一枚按了重试：房间拿着 File 再传一次。 */
  (e: 'retry-att', index: number): void
  (e: 'clear-reply'): void
  (e: 'clear-quote'): void
  (e: 'add-library-file', path: string): void
}>()

/** 正文。存在哪、谁清它、怎么跨刷新，全在房间那一层。 */
const draft = defineModel<string>({ required: true })

// 攒着的那几处批注（话题页注入的，见 `lib/pendingNotes`）。有它时输入框上方逐条列出来，
// 发送键写清这一下带几条；没有这个（私聊、别处的输入框）就照旧。
const pending = inject(PENDING_NOTES, null)
/** 篮里攒着几条（含只留评论的那种，托盘上要列）。 */
const pendingCount = computed(() => pending?.notes.value.length ?? 0)
/** 这一下按发送键真的会带走几条批注：要满足两条——去处是交给芝士的（只留评论的不经
 *  对话发），且没带附件（带附件时走的是附件 + 正文那条路，不含这一批）。不满足时发送
 *  键就不写「含 N 条批注」，写了就是承诺它做不到的事。
 *
 *  「带附件时不带这一批」这条规矩**只**写在下面这一行；旁边 `sendDraft` 判批量时用的
 *  就是它，不再另判一次附件——两处各写一遍的话，改一处漏一处就会一边说带得走、一边
 *  又不带。 */
const sendableCount = computed(() => (props.atts.length ? 0 : countSendable(pending?.notes.value ?? [])))
/** 光标在正文里的位置（第几个字符前面）。 */
const caret = ref(draft.value.length)

// 拖文件到输入栏 (spec §7.1)。只是把落区标出来，判断留给 usePendingAttachments。
const dragOver = ref(false)
// 只有真拖着文件才亮：拖一段选中的文字经过输入栏，落区亮起来是在承诺一件它不会
// 做的事。
function onDragOverFiles(e: DragEvent) {
  if (!e.dataTransfer?.types.includes('Files')) return
  dragOver.value = true
}
// dragleave 在指针移到**子元素**上时也会触发，所以一路拖过输入栏时落区会一路闪。
// relatedTarget 是指针进入的那个元素：它还在盒子里，就不算离开。
function onDragLeaveFiles(e: DragEvent) {
  const entering = e.relatedTarget
  if (entering instanceof Node && (e.currentTarget as HTMLElement).contains(entering)) return
  dragOver.value = false
}
function onDropFiles(e: DragEvent) {
  dragOver.value = false
  emit('drop-files', e)
}

// ---- 贴一大段纯文字 ----
//
// 一条消息装不下一份日志/一整段代码；贴进来之后输入框被它占满，人还得自己想法子
// 删回去。超过阈值就**就地**问一句要不要转成附件（不是弹窗）：忽略它，正文原样
// 留着——当前的粘贴行为不变，这只是多给一条路。
const LONG_PASTE_CHARS = 4000
const LONG_PASTE_LINES = 80
/** 刚贴进来、还没被选走的那一段：起点 + 原文，用来在转成附件时精确地把它拿掉。 */
const longPaste = ref<{ start: number; text: string } | null>(null)

function isLongPaste(text: string): boolean {
  return text.length > LONG_PASTE_CHARS || text.split('\n').length > LONG_PASTE_LINES
}

// 粘贴：先交给房间看有没有文件（贴进来的截图走上传那条路），再看得不是一段长文字。
// 文件优先——一张截图里没有正文，一件事只该有一个去处。房间认领了文件会
// preventDefault，这里就不再插手。
function onPaste(e: ClipboardEvent) {
  emit('paste', e)
  if (e.defaultPrevented) return
  const raw = e.clipboardData?.getData('text/plain') ?? ''
  if (!isLongPaste(raw)) return
  e.preventDefault()
  // 和平常的粘贴一样：\r\n 归一成 \n（Windows 上复制来的文本带 \r），光标落在刚贴进来的
  // 那一段之后。贴之前选中的字被这一段换掉。
  const text = raw.replace(/\r\n?/g, '\n')
  const start = caret.value
  const selected = composerInput.value?.selectedLength() ?? 0
  draft.value = draft.value.slice(0, start) + text + draft.value.slice(start + selected)
  caret.value = start + text.length
  longPaste.value = { start, text }
}

/** 忽略这条提议：正文一个字不动地留着。 */
function keepLongPaste() {
  longPaste.value = null
}

/** 把那一段从正文里拿掉，换成一份 .txt / .md 附件。 */
function pasteAsAttachment(ext: 'txt' | 'md') {
  const pasted = longPaste.value
  if (!pasted) return
  const { start, text } = pasted
  if (draft.value.slice(start, start + text.length) === text) {
    draft.value = draft.value.slice(0, start) + draft.value.slice(start + text.length)
  } else {
    // 人在这中间又打过字，偏移对不上了：退一步按内容找第一处。
    const idx = draft.value.indexOf(text)
    if (idx >= 0) draft.value = draft.value.slice(0, idx) + draft.value.slice(idx + text.length)
  }
  longPaste.value = null
  // 走的是挑文件/拖文件同一条路：上传、待发条、发送都由房间那一侧接手。
  emit('files', [new File([text], pastedTextName(ext), { type: ext === 'md' ? 'text/markdown' : 'text/plain' })])
  void nextTick(() => composerInput.value?.focus?.())
}

const composerInput = ref<InstanceType<typeof ComposerEditor> | null>(null)
const { beforeCaret } = useComposerText({
  draft,
  caret,
  expand: (text) => expandMentions(text),
  composing: () => !!composerInput.value?.composing(),
})
// 标签上写的名字。输入框里的 @ 发出去就会通知到人，所以画成通知过的那一种。
const chipNames = computed<RefNames>(() => {
  const mentionNames = { ...props.refs.mentionNames }
  for (const m of props.mentionPool) mentionNames[m.handle] ??= m.label
  if (props.agentSeat) mentionNames[props.agentSeat.handle] ??= props.agentSeat.label
  const topicTitles = { ...props.refs.topicTitles }
  for (const tp of props.topicList) topicTitles[tp.id] ??= tp.title
  return { mentionNames, topicTitles, notified: true, agentFaces: true }
})
const { mdAndUp } = useDisplay()
const mentionMenu = ref<InstanceType<typeof MentionMenu> | null>(null)

// @ 补全那一整套（候选、两级菜单、资料库、高亮）在 composable 里；这里只把光标
// 还给它，和把「挑中的是一份文件」转成往上发的那件事。
// 打 # 时能引用的文档：项目里的，第一次打 # 时按项目取一次。取不到就只列频道。
const linkable = ref<LinkableDocument[]>([])
let linkableFor: string | null = null
function documentList(): LinkableDocument[] {
  const projectId = props.topic?.project_id
  if (projectId && linkableFor !== projectId) {
    linkableFor = projectId
    linkable.value = []
    linkableDocuments(projectId)
      .then((docs) => {
        if (linkableFor === projectId) linkable.value = docs
      })
      .catch(() => {
        // 引用文档是输入时的一个便利，不是这条消息发不出去的理由。
        if (linkableFor === projectId) linkableFor = null
      })
  }
  return linkable.value
}

const picker = useRoomMentionPicker({
  draft: beforeCaret,
  writeTokens: true,
  topic: () => props.topic,
  mentionPool: () => props.mentionPool,
  topicList: () => props.topicList,
  documentList,
  focus: () => composerInput.value?.focus?.(),
  onAddLibraryFile: (path) => emit('add-library-file', path),
  // 资料库那一级的数据从这里进去：这个件本来就够得着接口层（它还要发、还要传，
  // 见 `lib/attachments.ts`），composable 本身够不着，也不该够着。
  listLibrary: searchLibraryFiles,
  // ↑/↓ 换完之后把那一项滚进视野。菜单自己有高度上限、自己滚，所以这件事问它。
  scrollActiveIntoView: () => mentionMenu.value?.scrollActiveIntoView(),
})
// 拆开拿：模板里读 `picker.menuOpen` 拿到的是那个 ref 本身，不是它的值。
const {
  matches: mentionMatches,
  level: mentionLevel,
  menuVisible: mentionMenuVisible,
  menuOpen: mentionMenuOpen,
  activeIndex: mentionActiveIndex,
} = picker

function pickActiveMention(): boolean {
  return picker.pickActive()
}

// Human composer: turn a friendly "@名字 / @话题名 / @handle" into the canonical
// token (<@handle> / <#topicId>) at send time. The rules live in the shared
// module so this stays identical to the backend's backstop.
function expandMentions(text: string): string {
  return expandComposerMentions(
    text,
    props.agentSeat,
    props.mentionPool,
    props.topicList.filter((t) => t.kind !== 'root')
  )
}

// 图片输入: paste (screenshot) or pick images; they upload to the topic's
// worktree immediately and wait in a preview strip until send.
// 房间里那位芝士 —— **房间名册**上坐着的那一行，不是项目名册上那行共用的。
//
// 叫不叫芝士，由**这条消息 @ 没 @ 它**决定 —— 和 @ 一个人走的是同一条路，
// 区别只在于 @ 人是通知、@ 它是真的开一轮。这以前是输入区上一个单独的开关：
// 芝士本来就在 @ 补全的名单里（`agent` 标记），于是同一个意图有两条并列的说法，
// 而只有开关那条是通的 —— 在正文里 @ 了它，它读得到，却不会动。
//
// 群播 (@all/@here) 不算：那是通知房间里的人，不是把活派给它。
//
// 认的是上面那一位的 handle，不是「名单里哪个带 AI 标记的 handle」：后者在名册
// 没到时认的是项目那位，于是正文里那个 @ 指不到房间里会动的人，而按钮和消息都写
// 着「叫了它」——两份说法，正是这个功能一开始要消灭的东西。
//
// 名册到了之后，@ 名单上别的 AI 队友也算叫：一个话题可以 @ 好几位，@ 到的那位
// 就是开这一轮的那位（不在房间里的会被请进来）。只认座位那一位时，@ 第二位队友
// 发出去，那颗按钮不亮，看起来就像只有芝士叫得动。
function mentionsAgent(expanded: string): boolean {
  return containsAgentMention(expanded, props.agentSeat, props.mentionPool)
}

// 这条草稿发出去交给谁：问后端（`useAddressing`）。问的就是发送时会发出去的那一份——展开
// 成 <@handle> 之后的正文、回复对象、有没有正文——所以它说的就是发出去之后会发生的事。
const { addressing, fresh: addressingFresh } = useAddressing({
  ask: () => ({
    place: props.place ?? null,
    draft: {
      content: expandMentions(draft.value),
      reply_to: props.replyTo ?? undefined,
      with_text: !!draft.value.trim() || !props.atts.length,
    },
  }),
  version: () => props.placeState ?? null,
})

// 这条草稿现在交不交给队友。**读的是后端对这份草稿的回答**，不是一个单独存着的开关值，
// 也不是浏览器照抄的一份规矩：@ 了它、回复它的消息、任务负责人在开着的任务里说话，
// 都由后端那一处判定。刚改过草稿、回答还在路上的那一下，先看正文里有没有 @ 它——点头像、
// ⌘/Ctrl+Enter 写进去的就是这一枚，按下去当场就要亮，不等一次来回。
function goesToAgent(expanded: string): boolean {
  if (addressingFresh.value) return !!addressing.value?.agent
  return mentionsAgent(expanded) || !!addressing.value?.agent
}
const summonOn = computed(() => goesToAgent(expandMentions(draft.value)))
// 这个房间的芝士是谁，现在知道了吗。
const summonReady = computed(() => props.agentSeat !== null)
// ⌘/Ctrl+Enter「发送并交给它」：和 AI 队友私聊时没有可交的人，这个组合键就是发送，
// 提示也不说交给谁。任务里照旧有：那里的 @ 选的是这件事交给哪一位。
const handOff = computed(() => summonReady.value && props.handOff !== false)
const keysTitle = computed(() => {
  if (!enterSends.value) return t('work.room.composer.pasteHint')
  return handOff.value
    ? t('work.room.composer.keysHint', { name: props.agentName })
    : t('work.room.composer.keysHintSend')
})

// ---- 交给谁：发送键左边那颗队友头像（ComposerSummon） ----
//
// 这个频道里能交给的 AI 队友：频道自己那位在前，其余是 @ 名单上的队友（别人自带的
// Claude Code 也在里面：@ 了它，平台会把它请进来并问它的主人，见后端 own_calls）。
type Mate = { handle: string; label: string }
const mates = computed<Mate[]>(() => {
  const list: Mate[] = props.agentSeat ? [props.agentSeat] : []
  for (const m of props.mentionPool)
    if (m.agent && !list.some((x) => x.handle === m.handle)) list.push({ handle: m.handle, label: m.label })
  return list
})
function mateOf(agent: { handle: string; name: string }): Mate {
  return mates.value.find((m) => m.handle === agent.handle) ?? { handle: agent.handle, label: agent.name }
}
// 正文里 @ 到的队友，按出现的顺序。
const draftMates = computed<Mate[]>(() => {
  const out: Mate[] = []
  for (const [, handle] of expandMentions(draft.value).matchAll(/<@([\w-]+)>/g)) {
    const mate = mates.value.find((m) => m.handle === handle)
    if (mate && !out.includes(mate)) out.push(mate)
  }
  return out
})
// 没 @ 任何一位时头像画谁、点一下交给谁：这个频道里上一次交给的那位，没有就是频道自己那位。
// 从选单里换了一位，这个频道之后就一直是它，直到再换。
const preferred = ref<string | null>(props.lastMate ?? null)
watch(
  () => props.topic?.id,
  () => (preferred.value = props.lastMate ?? null)
)
watch(
  () => props.lastMate,
  (handle) => {
    if (handle) preferred.value = handle
  }
)
const restingMate = computed<Mate | null>(
  () => mates.value.find((m) => m.handle === preferred.value) ?? mates.value[0] ?? null
)
// 名册还没到时头像照样画出来（按名字配色、灰着、点不动），这一行不会因为它晚到而跳一下。
// 没 @ 谁、却交给了一位（回复了它、任务里不用 @）：画的是那一位。
const shownMates = computed<Mate[]>(() => {
  if (draftMates.value.length) return draftMates.value.slice(0, 2)
  const agent = addressing.value?.agent
  return [agent ? mateOf(agent) : restingMate.value ?? { handle: '', label: props.agentName }]
})
// 任务负责人那里每句都交给队友：头像常亮，是规则不是开关。和 AI 队友私聊时没有这颗头像。
const summonFixed = computed(() => props.alwaysSummon && props.handOff !== false)
const summonShown = computed(() => !(props.alwaysSummon && props.handOff === false))
const peopleOnly = computed(() => !addressing.value?.agent && !!addressing.value?.people.length)
// 「交给：」后面那位队友叫什么：和头像、名册同一个叫法（`mateOf`），不是库里存的原名——
// 没改过名的队友库里存的是「芝士」，屏幕按读者的语言叫它。
const recipientMateName = computed(() => {
  const agent = addressing.value?.agent
  return agent ? mateOf(agent).label : ''
})
// 「交给：」后面写的人名：@ 名单上的叫法，名单上没有就是 handle。
function personName(handle: string): string {
  return props.mentionPool.find((m) => m.handle === handle)?.label ?? handle
}

// ---- 打字的时候任务关了 ----
//
// 开着的任务里，负责人说的每句话都交给队友；任务一关，同一句话就只是讨论。打到一半的
// 草稿不能就这样悄悄降成讨论发出去：那个人一直看着「交给」那一行在打字。所以记住这份
// 草稿是在「任务交给队友」时写的（`heldFor`），任务关了之后第一次发，先就地问一句：
// 「@ 它」还是「仅发布讨论」。草稿、回复对象、附件都原样留着。
//
// 记的是**这个人看着的那一行**：草稿非空的每一刻，那一行说「任务里交给队友」就记下。所以
// 第一个字落下时那一行（空草稿问到的那一次）已经说了交给队友，就从这一刻算起——不等这份
// 草稿自己的回答；一路打字、它第一次问到时任务已经关了，也照样先问。
const heldFor = ref<Mate | null>(null)
const askingRecipient = ref(false)
watch([addressing, () => !!draft.value.trim()], ([now, typed]) => {
  if (!typed) {
    heldFor.value = null
    askingRecipient.value = false
    return
  }
  if (!now) return
  if (now.reason === 'task' && now.agent) heldFor.value = mateOf(now.agent)
  else if (now.task_status !== 'closed') heldFor.value = null
})
watch(
  () => props.place,
  () => {
    heldFor.value = null
    askingRecipient.value = false
  }
)
/** 这一下发出去会不会把「交给队友」悄悄降成讨论。 */
function wouldDowngrade(): boolean {
  const now = addressing.value
  return !!heldFor.value && !!now && !now.agent && now.task_status === 'closed'
}
function sendToHeldMate() {
  const mate = heldFor.value
  heldFor.value = null
  askingRecipient.value = false
  sendDraft({ summon: true, to: mate })
}
function postAsDiscussion() {
  heldFor.value = null
  askingRecipient.value = false
  sendDraft()
}
/** 送到时后端说这条已经不交给队友了（`AddressingChanged`）：房间把草稿放回来之后叫这个，
 *  就地问同一句。 */
function askRecipient(now: Addressing | null) {
  const shown = heldFor.value ?? (addressing.value?.agent ? mateOf(addressing.value.agent) : null)
  heldFor.value = shown ?? restingMate.value
  askingRecipient.value = !!heldFor.value && !now?.agent
}

// 名册还没到的时候不能替人写这个 @：召唤与否是浏览器按**能不能把名字解析成
// handle** 算出来的，此刻解析不出来，写进去的 @ 只是一行字，消息照发、它照样不
// 动。所以这几个入口在那一瞬间是关着的（见 `summonReady`），宁可少一个入口，
// 也不要一个点了不算数的入口。
function withAgentMention(text: string, mate: Mate | null = restingMate.value): string {
  if (!mate || mentionsAgent(expandMentions(text))) return text
  return `<@${mate.handle}> ${text}`
}

// 摘掉正文里对这位的第一处 @。只摘第一处：正文里别处还提着它（「照 @芝士 说的改」）
// 是在说事，不是在叫它，取消这一次召唤不该顺手把那句话也改了。
function withoutMention(text: string, mate: Mate): string {
  for (const pat of [`<@${mate.handle}>`, `@${mate.label}`, `@${mate.handle}`]) {
    const at = text.indexOf(pat)
    if (at < 0) continue
    const after = at + pat.length
    return text.slice(0, at) + text.slice(text[after] === ' ' ? after + 1 : after)
  }
  return text
}

// 点一下头像：它不改任何隐藏状态，它只是替你打那个 @，写完你看得见、也能自己删掉。
function toggleSummon() {
  if (!draftMates.value.length) draft.value = withAgentMention(draft.value)
  else draft.value = draftMates.value.reduce(withoutMention, draft.value)
  void nextTick(() => composerInput.value?.focus?.())
}

// 从选单里换一位：正文里那枚标签换成这一位。
function pickMate(handle: string) {
  const mate = mates.value.find((m) => m.handle === handle)
  if (!mate) return
  preferred.value = handle
  draft.value = withAgentMention(draftMates.value.reduce(withoutMention, draft.value), mate)
  void nextTick(() => composerInput.value?.focus?.())
}
function summonOff() {
  draft.value = draftMates.value.reduce(withoutMention, draft.value)
  void nextTick(() => composerInput.value?.focus?.())
}

// 输入法（IME）那一下按键的判定：认的是**这一次按键本身**是不是上屏/选字那一下，
// 不是「离上屏近不近」。
//
// 上屏那一下按的键，Chromium 报 isComposing，Safari 在 compositionend 之后补的尾巴
// keydown 报 keyCode 229（它甚至比 compositionend 还晚，所以自己记的那份 `composing`
// 那时已经是 false）；普通的发送回车两个都不带。所以这三条就够。
//
// 曾经这里多一条「compositionend 之后 100 毫秒内的按键都算输入法」的时间窗。它把上屏
// 之后紧接着按的、真要发送的那个回车也吃掉——打字快的人每次上屏后的回车都变成换行
// （2026-10-24 反馈）。窗口按毫秒算就必然如此：同一次输入法里「上屏」和「发送」是两次
// 相邻的按键，快的只隔几十毫秒，没有时间窗能分开它们，能分开它们的是事件本身的属性。
// 仓库里其余同类判断（`MessageEditor.vue` 等二十来处）也都是只看事件属性，没有时间窗。
let composing = false
function onCompositionStart() {
  composing = true
}
function onCompositionEnd() {
  composing = false
}
function isImeKey(e: KeyboardEvent) {
  return composing || e.isComposing || e.keyCode === 229
}

// 触摸屏上回车是换行。软键盘没有 Shift 这一层，所以「Enter 发送 / Shift+Enter
// 换行」在手机上等于「打不出第二行」——发送有按钮，换行没有别的办法。
// 按输入方式判断，不按视口宽度：带触摸屏的笔记本两样都对。
const coarse = typeof window !== 'undefined' ? window.matchMedia?.('(hover: none)') : undefined
const enterSends = ref(!coarse?.matches)
coarse?.addEventListener?.('change', (e: MediaQueryListEvent) => (enterSends.value = !e.matches))

function onComposerKey(e: KeyboardEvent) {
  if (onVoiceKey(e)) return
  // 翻进资料库之后，Esc 是退回一级的那一步（而不是把整个菜单关掉——@ 还在正文里）。
  // ← 也是；`@` 后面什么都没打时的退格也是——那一下要是删掉了 `@`，整个菜单就没了，
  // 人只是想回上一级。输入法选字时这几个键是给输入法的。
  const back =
    e.key === 'Escape' ||
    ((e.key === 'ArrowLeft' || (e.key === 'Backspace' && picker.query.value === '')) && !isImeKey(e))
  if (back && mentionLevel.value === 'library') {
    e.preventDefault()
    picker.backToRoot()
    return
  }
  // 菜单里有东西可挑时上下键换高亮。输入法正在选字时这两个键是给输入法的（翻候选
  // 词），所以那会儿让过去。空态（一个候选都没有）和菜单收起时不拦——那时上下键该
  // 去挪光标，而不是隔着一块看不见的名单选人。
  const arrow = e.key === 'ArrowDown' || e.key === 'ArrowUp'
  if (arrow && mentionMenuOpen.value && !isImeKey(e)) {
    e.preventDefault()
    picker.move(e.key === 'ArrowDown' ? 1 : -1)
    return
  }
  // Esc 收起菜单：有候选时收起候选，空态时把那句「暂无匹配」也收起来——都是「这次
  // @ 先不挑了」，`@` 半截字留在正文里。
  if (e.key === 'Escape' && mentionMenuVisible.value) {
    e.preventDefault()
    picker.close()
    return
  }
  if (e.key !== 'Enter' || e.shiftKey) return
  // IME composition (拼音选字/上屏) 的回车是按给输入法的，绝不当成发送。
  if (isImeKey(e)) return
  // ⌘/Ctrl+Enter = 发送并交给芝士，正文里一个 @ 都不用打。判断排在 @-菜单前面：
  // 打到一半的 @ 不该把这个已经说清楚的「交给它」变成一次选人。
  if ((e.metaKey || e.ctrlKey) && handOff.value) {
    e.preventDefault()
    sendDraft({ summon: true })
    return
  }
  // While the @-menu is open, Enter picks the highlighted match instead of sending.
  if (pickActiveMention()) {
    e.preventDefault()
    return
  }
  if (!enterSends.value) return
  e.preventDefault()
  sendDraft()
}

// Keyed on the topic's id, NOT the object reference: the parent replaces
// `topics.value` wholesale on every refreshTopics() (e.g. after each agent
// turn), which mints a brand-new object for the SAME topic. Watching the
// object itself made every turn look like a topic switch — full reconnect,
// history reload, composer disabled mid-reconnect (which blurs it). Only a
// real id change is a real switch.

// `summon: true` = ⌘/Ctrl+Enter「发送并交给它」。它把 @ 写进正文再发，而不是在帧
// 上把 summon 悄悄置真：时间线上那条消息必须自己说明它叫了谁，否则读的人看到的
// 是一条谁也没 @ 的消息，芝士却动了。
function sendDraft(opts?: { summon?: boolean; to?: Mate | null }) {
  if (props.attsUploading) return
  // 攒着待发批注时，这一下发的就是那一批：输入框里的字当作「总体说明」并进同一条消息，
  // 而不是先发一条空话再发那批（原型里发送键写的就是「发送（含 N 条批注）」）。
  if (sendableCount.value > 0) {
    const summary = expandMentions(draft.value)
    if (pending?.send(summary)) {
      draft.value = ''
      // 引用条和正文一样，是这一条消息带着的东西：这一批发出去就算用掉了。走
      // `emit('send')` 那条老路时由 `useChatComposer` 清（它调 `clearDraftQuote`），
      // 这条捷径绕过了它——不清的话，选着的那一处会搭在下一条消息上。
      emit('clear-quote')
      return
    }
    // 没发出去（对话栏那一刻收不下）：这一批留着，输入框里的字也留着，按原样退回去。
    // 不往下走老路——那颗键写的是「发送（含 N 条批注）」，只把正文发出去、把这一批留在
    // 原地，读者会以为整批都发走了。失败那句话说在托盘上（`pending.failed`，三个发送入口
    // 共用一条），所以这里不必再报一次。
    return
  }
  if (!draft.value.trim() && !props.atts.length) return
  if (!opts?.summon && wouldDowngrade()) {
    askingRecipient.value = true
    return
  }
  const content = expandMentions(
    opts?.summon ? withAgentMention(draft.value, opts.to ?? restingMate.value) : draft.value
  )
  // 那一行此刻写着交给谁，发出去就是在说「我要交给它」：送到时不交给了，后端不发、退回来
  // （`expects_agent`），由后端按发送那一刻的状态再判一次。带的是屏幕上那一行，不论它是
  // 不是对这份草稿问到的——刚改了一个字就按发送，那人看着的仍是那一行。
  const expectsAgent = !opts?.summon && !!addressing.value?.agent
  emit('send', {
    content,
    summon: opts?.summon ? mentionsAgent(content) || summonOn.value : summonOn.value,
    expectsAgent,
  })
}

// ---- 语音输入（useComposerVoice）----
const {
  VOICE_MAX_SECONDS,
  canVoice,
  voice,
  voiceOpen,
  voiceSwap,
  swapVoice,
  onVoice,
  onVoiceKey,
  holding,
  dragX,
  armed,
} = useComposerVoice({
  draft,
  caret,
  editor: composerInput,
  enabled: () => !!props.topic && !!props.hearVoice,
  hear: (wav) => props.hearVoice!(wav),
  expand: (text) => expandMentions(text),
  isImeKey,
})

// 「提醒我」：对话框管填和发，这里只开它，和设好之后说一声几点会提醒。
const reminderOpen = ref(false)
function onReminderSet(at: Date) {
  const when = new Intl.DateTimeFormat(i18n.global.locale.value, {
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(at)
  toast.success(t('work.room.reminder.set', { when }))
}

// 发送键亮不亮：有字、有东西跟着走，或者攒着待发批注（那一下发的是那一批）。
const canSend = computed(() => !!draft.value.trim() || props.atts.length > 0 || pendingCount.value > 0)

defineExpose({
  /** @ 完人、点完按钮、起手草稿写完之后要把光标还回来——这是最烦人的一处。 */
  focus() {
    void nextTick(() => composerInput.value?.focus?.())
  },
  /** 在正文前面 @ 上这个频道平时交给的那位（「回复并 @芝士」）。已经 @ 了队友就不动。 */
  addAgentMention() {
    if (summonReady.value) draft.value = withAgentMention(draft.value)
    void nextTick(() => composerInput.value?.focus?.())
  },
  askRecipient,
})
</script>

<template>
  <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- a drop target for files dragged in; the attach button is the keyboard's way -->
  <div
    class="composer"
    :class="{ 'composer--drop': dragOver }"
    @dragenter.prevent="onDragOverFiles"
    @dragover.prevent="onDragOverFiles"
    @dragleave="onDragLeaveFiles"
    @drop.prevent="onDropFiles"
  >
    <!-- @-autocomplete: 没打字是「人 / 群播 / 资料库」这一级，打了字就是搜索。 -->
    <MentionMenu
      ref="mentionMenu"
      :open="mentionMenuVisible"
      :matches="mentionMatches"
      :active-index="mentionActiveIndex"
      :level="mentionLevel"
      :enter-sends="enterSends"
      @pick="picker.pick"
      @hover="picker.hover"
      @back="picker.backToRoot"
    />
    <!-- 输入框上方只有一种提示条，同一时刻只有一条，贴在盒子顶上：语音没成、贴了一大段、
         @ 了不在频道里的人（外面从 `strip` 插槽交进来）。就地说，不弹窗；忽略它，正文原样留着。 -->
    <Transition name="composer-strip">
      <div v-if="voice.trouble.value" key="voice" class="composer-strip composer-strip--danger" role="status">
        <v-icon size="16">{{
          voice.trouble.value.kind === 'denied' ? 'mdi-microphone-off' : 'mdi-alert-circle-outline'
        }}</v-icon>
        <span class="composer-strip__text">
          {{
            voice.trouble.value.kind === 'denied'
              ? t('work.room.composer.voice.denied')
              : voice.trouble.value.message || t('work.room.composer.voice.failed')
          }}
        </span>
        <button
          type="button"
          class="composer-strip__x"
          :aria-label="t('work.room.composer.voice.dismiss')"
          :title="t('work.room.composer.voice.dismiss')"
          @click="voice.trouble.value = null"
        >
          <v-icon size="14">mdi-close</v-icon>
        </button>
      </div>
      <!-- A very long plain-text paste: offer to carry it as a file instead of
           flooding the box. Ignoring it keeps the text exactly as pasted. -->
      <div v-else-if="longPaste" key="paste" class="composer-strip">
        <v-icon size="16">mdi-file-document-outline</v-icon>
        <span class="composer-strip__text">
          {{ t('work.room.composer.longPaste.offer', { count: longPaste.text.length }) }}
        </span>
        <BaseButton kind="ghost" size="sm" @click="pasteAsAttachment('txt')">
          {{ t('work.room.composer.longPaste.txt') }}
        </BaseButton>
        <BaseButton kind="ghost" size="sm" @click="pasteAsAttachment('md')">
          {{ t('work.room.composer.longPaste.md') }}
        </BaseButton>
        <button
          type="button"
          class="composer-strip__x"
          :aria-label="t('work.room.composer.longPaste.keep')"
          :title="t('work.room.composer.longPaste.keep')"
          @click="keepLongPaste"
        >
          <v-icon size="14">mdi-close</v-icon>
        </button>
      </div>
      <div v-else-if="askingRecipient && heldFor" key="recipient" class="composer-strip" role="status">
        <span class="composer-strip__text">
          {{ t('work.room.composer.recipient.closedAsk', { name: heldFor.label }) }}
        </span>
        <BaseButton kind="ghost" size="sm" @click="sendToHeldMate">
          {{ t('work.room.composer.recipient.closedAskAgent', { name: heldFor.label }) }}
        </BaseButton>
        <BaseButton kind="ghost" size="sm" @click="postAsDiscussion">
          {{ t('work.room.composer.recipient.closedAskPost') }}
        </BaseButton>
      </div>
      <div v-else-if="$slots.strip" key="outside" class="composer-strip composer-strip--slot">
        <slot name="strip" />
      </div>
    </Transition>
    <!-- 攒着的待发批注逐条列在输入框上方：加了几处、分别是哪一处、要改什么，一眼看得全；
         一次发出去，芝士一轮改完。 -->
    <PendingNotesTray class="composer-pending" />
    <!-- 输入区是一个控件，不是浮在页面上的几个零件：一个圆角描边的盒子把
             「待发的图片 + 输入框 + 动作」框成一块。盒子自己就是和时间线之间的
             分隔，所以上面那条 divider 没了。 -->
    <div v-guide-anchor="guided ? 'composer-input' : ''" class="composer-box">
      <ComposerRecipient :addressing="addressing" :agent-name="recipientMateName" :name-of="personName" />
      <!-- 这条消息带着的东西：回复的那条在最前，然后是引用，后面是待发的附件。 -->
      <ComposerChipRow
        :reply-label="replyLabel"
        :quote="quote"
        :atts="atts"
        :topic-id="topic?.id ?? null"
        @remove-att="(i: number) => emit('remove-att', i)"
        @retry-att="(i: number) => emit('retry-att', i)"
        @clear-reply="emit('clear-reply')"
        @clear-quote="emit('clear-quote')"
      />
      <!-- 输入框独占一整行。它旁边并排放按钮时，真正能打字的那块在手机上只剩
             半屏——而按钮的数量只会往上加。 -->
      <ComposerEditor
        ref="composerInput"
        v-model="draft"
        v-model:caret="caret"
        class="composer-input"
        :names="chipNames"
        :placeholder="sendableCount > 0 ? t('work.room.preview.pendingSummaryPlaceholder') : hint"
        :label="hint"
        :title="keysTitle"
        @keydown="onComposerKey"
        @paste="onPaste"
        @compositionstart="onCompositionStart"
        @compositionend="onCompositionEnd"
      />
      <ComposerActions
        :guided="!!guided"
        :uploading="attsUploading"
        :can-send="canSend"
        :pending-count="sendableCount"
        :show-image-picker="!mdAndUp"
        :enter-sends="enterSends"
        :can-checklist="!!postChecklist"
        :can-remind="!!topic"
        :can-voice="canVoice"
        :voice="voice.state.value"
        :voice-swap="voiceSwap"
        @files="emit('files', $event)"
        @checklist="checklistOpen = true"
        @remind="reminderOpen = true"
        @send="sendDraft()"
        @voice="onVoice"
        @voice-done="voice.finish()"
        @voice-swap="swapVoice"
      >
        <template #chips><ComposerViewing :suppressed="!!quote" /><slot name="composer-chips" /></template>
        <template v-if="summonShown" #summon>
          <ComposerSummon
            :guided="!!guided"
            :mates="mates"
            :shown="shownMates"
            :on="summonOn"
            :fixed="summonFixed"
            :ready="summonReady"
            :busy="busyAgents ?? []"
            :people-only="peopleOnly"
            @toggle="toggleSummon"
            @pick="pickMate"
            @off="summonOff"
          />
        </template>
        <!-- 录音条：从麦克风的位置往左展开成一整行，说完了再收回麦克风。 -->
        <template #voice>
          <Transition name="voice-grow" :duration="{ enter: 320, leave: 180 }">
            <ComposerVoiceBar
              v-if="voiceOpen"
              :state="voice.state.value"
              :elapsed="voice.elapsed.value"
              :levels="voice.levels.value"
              :max-seconds="VOICE_MAX_SECONDS"
              :holding="holding"
              :drag-x="dragX"
              :armed="armed"
              @cancel="voice.cancel()"
            />
          </Transition>
        </template>
      </ComposerActions>
      <ComposerChecklistDialog v-if="postChecklist" v-model="checklistOpen" :post="postChecklist" />
    </div>
    <ReminderDialog v-if="topic" v-model="reminderOpen" :topic-id="topic.id" @set="onReminderSet" />
  </div>
</template>

<style scoped>
.composer {
  /* @ 菜单按它定位（见 MentionMenu.vue 的 .mention-menu）。 */
  position: relative;
  background: var(--surface);
  /* 内边距写在这里，不用 Vuetify 的 `pa-2 px-3`：那两个工具类带 `!important`，会把
     下面这一条安全区一起盖掉，手机上输入框于是照旧被 Home 横杠压住。上下 8px、左右
     12px 就是那两个类本来给的值。
     手机底部那一条圆角/横杠区（安全区）会压在输入框上。桌面上这个值是 0。 */
  padding: 8px 12px calc(8px + env(safe-area-inset-bottom));
}
/* 待发篮和输入框之间那一道缝：`.composer` 是纯块级（没有 gap），不加这一条两块的
   描边会贴在一起，看着像一个盒子里挤了两块东西。只给篮子加，别动 `.composer`——
   上面那条提示条是贴着输入框圆角顶的，拉开缝那对圆角就不成立了。 */
.composer-pending {
  margin-bottom: 8px;
}
/* 输入框上方的提示条：贴在盒子顶上的一片，安静的底。同一时刻只有一条（模板里是
   v-if 链），所以它们用同一种样子——语音没成、长文转附件、@ 了频道外的人。 */
.composer-strip {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 8px;
  margin: 0 8px;
  padding: 4px 4px 4px 12px;
  border-radius: var(--radius-lg) var(--radius-lg) 0 0;
  background: var(--fill);
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
.composer-strip--danger {
  background: var(--danger-wash);
  color: var(--danger-ink);
}
.composer-strip--slot {
  padding: 0;
}
.composer-strip--slot > :deep(*) {
  flex: 1;
}
.composer-strip__text {
  flex: 1 1 auto;
  min-width: 0;
  padding: 4px 0;
}
.composer-strip__x {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border: none;
  background: none;
  border-radius: var(--radius-sm);
  color: inherit;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.composer-strip__x:hover {
  background: var(--fill-2);
}
/* 长出来、收回去都是高度过渡（0fr → 1fr 那一招在 Transition 里换成 max-height 不行：
   高度不定），这里只动透明度和一点位移，离场快一档。 */
.composer-strip-enter-active {
  transition:
    opacity var(--dur-base) var(--ease-out),
    transform var(--dur-base) var(--ease-out);
}
.composer-strip-leave-active {
  transition:
    opacity var(--dur-quick) var(--ease-in),
    transform var(--dur-quick) var(--ease-in);
}
.composer-strip-enter-from,
.composer-strip-leave-to {
  opacity: 0;
  transform: translateY(4px);
}
/* 输入区是一个控件。原来输入框和几颗按钮各自浮在页面上，读起来是几个零件而不是
   一件东西——一个圆角描边就把它们收成一块，顺带替掉了上面那条 divider。 */
.composer-box {
  padding: 4px 6px 4px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  transition:
    border-color var(--dur-quick) var(--ease-standard),
    box-shadow var(--dur-quick) var(--ease-standard),
    background-color var(--dur-quick) var(--ease-standard);
}
/* 聚焦时那条边只提一档：--muted 是正文级的灰，一压就把整个盒子变成了主角。 */
.composer-box:focus-within {
  border-color: var(--faint);
}
/* 拖着文件进来时，变的是输入框自己那圈边：加深成实线，再垫一层底色。
   虚线读起来像占位、像还没定，而这一刻要说的是「就是这儿」；描在这个盒子上而不是
   外面那层，是因为盒子本来就是那个控件，它的圆角也已经在那儿了。
   第二像素靠 box-shadow 加，不靠 border-width——后者会改盒子尺寸，把输入框顶一下。
   这条排在 :focus-within 后面：拖进来的时候光标通常就在输入框里，两条同权，后面
   的赢。 */
/* 录音条从麦克风的位置往左展开成整行（320ms 减速），说完了 180ms 加速收回麦克风。
   收起时的形状就是麦克风那一格：28px 宽，贴着右边。 */
.voice-grow-enter-active :deep(.voice-bar__body) {
  transition:
    clip-path var(--dur-slow) var(--ease-out),
    opacity 120ms var(--ease-out);
}
.voice-grow-leave-active :deep(.voice-bar__body) {
  transition:
    clip-path 180ms var(--ease-in),
    opacity 180ms var(--ease-in);
}
.voice-grow-enter-from :deep(.voice-bar__body),
.voice-grow-leave-to :deep(.voice-bar__body) {
  clip-path: inset(0 0 0 calc(100% - 28px) round var(--radius-md));
  opacity: 0;
}
.composer--drop .composer-box {
  border-color: var(--muted);
  box-shadow: inset 0 0 0 1px var(--muted);
  background: var(--fill);
}
</style>
