<script setup lang="ts">
// 输入区：打字、@ 补全、贴/拖/挑文件、以及「交给芝士」。
//
// **叫不叫芝士，只由正文里有没有 @ 它决定**，这里没有一个自己存着状态的开关。
// 开关能和正文说不一样的话（亮着、正文里却没有 @），那时候「这条到底算不算叫了
// 它」谁也答不上来，而只有开关那条是通的。三个入口（手打 @、点按钮、⌘/Ctrl+
// Enter）写的都是同一个 @，你看得见，也能自己删。
//
// 草稿、回复对象和待发附件**不归它管**：那几样要跟着话题走、跨刷新活下来，而话题
// 什么时候换只有房间知道。正文是 v-model，其余由房间传进来——它只负责画和发。
import type { LibraryFile } from '../../api'
import type { ChatAttachment, Topic } from '../../cx_types'

import { computed, nextTick, ref, watch } from 'vue'
import { useDisplay } from 'vuetify'

import { listProjectLibrary } from '../../api'
import { expandMentions as expandMentionNames, mentionsHandle } from '../../lib/expandMentions'
import { IMAGE_SUFFIXES, suffixOf } from '../../lib/fileKind'
import ExternalTag from '../common/ExternalTag.vue'

import AttachmentChip from './AttachmentChip.vue'
import ComposerChip from './ComposerChip.vue'

import { t } from '@/i18n'

const props = defineProps<{
  topic: Topic | null
  /** @ 得到的人：这个房间里的，加上项目里还没进这个房间的。 */
  mentionPool: { handle: string; label: string; agent: boolean; external?: boolean }[]
  /** @ 得到的话题，用来把「@话题名」展开成 <#id>。 */
  topicList: Topic[]
  /** 这个房间交给的那位 AI 队友。名册还没到时是 null，两个召唤入口都关着。 */
  agentSeat: { handle: string; label: string } | null
  agentName: string
  /** 和芝士私聊：每条都是说给它听的，没有「交给它」这颗按钮。 */
  alwaysSummon: boolean
  /** 输入框那一行提示语。 */
  hint: string
  /** 已经传上去、等着跟下一条一起发出去的附件。 */
  atts: ChatAttachment[]
  attsUploading: boolean
  /** 这条消息回复的是哪一条，读出来的样子（「回复 谁：说了什么」）。不回复时是 null。 */
  replyLabel?: string | null
}>()

const emit = defineEmits<{
  /** 发这一条。附件由房间补上——它才知道此刻待发条里有什么。 */
  (e: 'send', message: { content: string; summon: boolean }): void
  (e: 'files', files: File[]): void
  (e: 'drop-files', event: DragEvent): void
  (e: 'paste', event: ClipboardEvent): void
  (e: 'remove-att', index: number): void
  (e: 'clear-reply'): void
  (e: 'add-library-file', path: string): void
}>()

/** 正文。存在哪、谁清它、怎么跨刷新，全在房间那一层。 */
const draft = defineModel<string>({ required: true })

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
const composerInput = ref<{ focus?: () => void } | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const imageInput = ref<HTMLInputElement | null>(null)
const { mdAndUp } = useDisplay()
function pickFiles() {
  fileInput.value?.click()
}
// 手机上单开一个「照片」：系统的文件选择器里翻相册要好几步，而 accept=image/*
// 直接进相册/相机。桌面上不给这一颗——那儿贴一张截图或者拖进来就完事了。
function pickImages() {
  imageInput.value?.click()
}
function onFilePicked(e: Event) {
  const input = e.target as HTMLInputElement
  if (input.files?.length) emit('files', Array.from(input.files))
  input.value = '' // allow re-picking the same file
}
// @-autocomplete (§3.1.1 人也能 @): the @token being typed at the end of the
// draft, and the teammates / topics / broadcast tokens it can complete to.
// Mirrors TopicView's composer so the root-topic and 私聊 composers get the
// same picker.
const mentionQuery = computed(() => {
  const m = draft.value.match(/@([^\s@]*)$/)
  return m ? m[1] : null
})
interface MentionItem {
  label: string
  kind: 'member' | 'topic' | 'broadcast' | 'file' | 'category'
  // Text written after the "@" when picked (a handle/name/token).
  insert: string
  // Secondary line: @handle for people, status for topics, hint for broadcast.
  sub: string
  agent: boolean
  // 团队以外、被邀请进这个项目的人：候选里挂「外部」，@ 之前就知道他不是自己人。
  external?: boolean
  // 二级菜单里这一项属于哪一组（同一组的标题只画一次）。
  group?: string
}
// 群播 (fusion-design §3): @all/@here are FIXED-LITERAL tokens (rule 4), pinned
// at the top. expandMentions turns them into <@all>/<@here>.
const BROADCAST_ITEMS: MentionItem[] = [
  { label: '所有人', kind: 'broadcast', insert: 'all', sub: '@all · 通知话题全体成员', agent: false },
  { label: '在线成员', kind: 'broadcast', insert: 'here', sub: '@here · 通知在线成员', agent: false },
]
// 资料库：项目给进来的文件，@ 一下就能带上这条消息。按需拉一次——打开一个房间的
// 人不一定要引用文件，而打了 @ 的人正要挑东西。
const libraryFiles = ref<LibraryFile[]>([])
const libraryFor = ref<string | null>(null)
// 菜单在第几级。没打字的时候资料库只是一行入口（`category`）：一个项目的文件会比
// 房间里的人多得多，平铺进来等于把「@ 一个人」这件事挤掉。打了字就不分级了——那时
// 人要的是搜索，人、话题、文件一起找。
const mentionLevel = ref<'root' | 'library'>('root')
// 这一格里「算不算图片」比预览域宽：gif / webp 浏览器也画得出来，而这里只是分组。
const PICKER_IMAGE_SUFFIXES = new Set([...IMAGE_SUFFIXES, 'gif', 'webp'])
function libraryItems(ql: string): MentionItem[] {
  const rows = libraryFiles.value.filter((f) => f.path.toLowerCase().includes(ql))
  const item = (f: LibraryFile, group: string): MentionItem => ({
    label: f.path,
    kind: 'file',
    insert: f.path,
    sub: group,
    agent: false,
    group,
  })
  return [
    ...rows.filter((f) => !PICKER_IMAGE_SUFFIXES.has(suffixOf(f.path))).map((f) => item(f, '文件')),
    ...rows.filter((f) => PICKER_IMAGE_SUFFIXES.has(suffixOf(f.path))).map((f) => item(f, '图片')),
  ]
}
async function loadLibrary() {
  const projectId = props.topic?.project_id
  if (!projectId || libraryFor.value === projectId) return
  libraryFor.value = projectId
  try {
    libraryFiles.value = (await listProjectLibrary(projectId)).data
  } catch {
    // 挑文件是输入栏里的一个便利，不是这条消息发不出去的理由。
    libraryFiles.value = []
    libraryFor.value = null
  }
}
watch(
  () => props.topic?.project_id,
  () => {
    libraryFiles.value = []
    libraryFor.value = null
  }
)
watch(
  () => mentionQuery.value !== null,
  (open) => {
    if (open) void loadLibrary()
    // 菜单关了就回到一级：下一次打 @ 的人不该落在上一次翻到的地方。
    else mentionLevel.value = 'root'
  },
  // immediate: 草稿是恢复出来的（上次在这个房间打了一半的 @），输入区建起来的
  // 那一刻菜单就已经是开着的。只等「变成开着」的话，这一次永远等不到，菜单开着
  // 却一份文件都列不出来。
  { immediate: true }
)

const mentionMatches = computed<MentionItem[]>(() => {
  const q = mentionQuery.value
  if (q === null) return []
  const ql = q.toLowerCase()
  if (mentionLevel.value === 'library') return libraryItems(ql).slice(0, 12)
  const broadcast = BROADCAST_ITEMS.filter((b) => b.insert.startsWith(ql) || b.label.includes(q))
  const named: MentionItem[] = [
    ...props.mentionPool.map((m) => ({
      label: m.label,
      kind: 'member' as const,
      insert: m.label,
      sub: `@${m.handle}`,
      agent: m.agent,
      external: !!m.external,
    })),
    ...props.topicList
      .filter((t) => t.kind !== 'root')
      .map((t) => ({
        label: t.title,
        kind: 'topic' as const,
        insert: t.title,
        sub: t.status === 'archived' ? '已归档' : '进行中',
        agent: false,
      })),
  ].filter((i) => i.label.toLowerCase().includes(ql))
  // Agent 排在最前，群播让位。第一格就是 Enter 的默认答案，而「打一个 @ 然后回
  // 车」在这个产品里压倒性地是「交给芝士」——把 @all 摆在那个位置，等于让最常见
  // 的一次输入默认去打扰整个话题的所有人。群播是 fixed-literal token，换个位置
  // 它还是那两个 token。
  const agents = named.filter((i) => i.agent)
  const rest = named.filter((i) => !i.agent)
  // 没打字：资料库是一行入口。打了字：文件和人、话题一起被搜出来。
  const files = ql ? libraryItems(ql) : []
  const library: MentionItem[] =
    !ql && libraryFiles.value.length
      ? [
          {
            label: '资料库',
            kind: 'category',
            insert: 'library',
            sub: `${libraryFiles.value.length} 份文件`,
            agent: false,
          },
        ]
      : []
  return [...agents, ...library, ...broadcast, ...rest, ...files].slice(0, 7)
})
function pickMention(item: MentionItem) {
  if (item.kind === 'category') {
    mentionLevel.value = 'library'
    void nextTick(() => composerInput.value?.focus?.())
    return
  }
  if (item.kind === 'file') {
    // 文件不是一个能 @ 的人：挑中它是把它附在这条消息上，所以那个 @ 连同半个
    // 名字都从正文里拿掉，文件去待发条里待着。
    draft.value = draft.value.replace(/@([^\s@]*)$/, '')
    emit('add-library-file', item.insert)
    void nextTick(() => composerInput.value?.focus?.())
    return
  }
  draft.value = draft.value.replace(/@([^\s@]*)$/, `@${item.insert} `)
  // 挑完一个人，正是你要接着往下打字的时刻。鼠标点菜单会把焦点带到那颗按钮上，
  // 键盘挑则让整块菜单从 DOM 里消失——两条路都可能把光标从输入框里带走，而「@
  // 完人还要再点一次输入框」是这个面板最烦人的地方。
  void nextTick(() => composerInput.value?.focus?.())
}

// Human composer: turn a friendly "@名字 / @话题名 / @handle" into the canonical
// token (<@handle> / <#topicId>) at send time. The rules live in the shared
// module so this stays identical to the backend's backstop.
function expandMentions(text: string): string {
  return expandMentionNames(
    text,
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
function mentionsAgent(expanded: string): boolean {
  return mentionsHandle(expanded, props.agentSeat?.handle)
}

// 这条草稿现在叫不叫它。**读的是正文**，不是一个单独存着的开关值：真相只有一条，
// 入口可以有三个（手打 @、点按钮、⌘/Ctrl+Enter 都是往正文里写同一个 @）。
// 独立开关是另一回事 —— 那种东西能和正文说不一样的话（开关亮着、正文里没有 @），
// 那时候「这条到底算不算叫了它」谁也答不上来，而只有开关那条是通的。
const summonOn = computed(() => props.alwaysSummon || mentionsAgent(expandMentions(draft.value)))
// 这个房间的芝士是谁，现在知道了吗。
const summonReady = computed(() => props.agentSeat !== null)

// 名册还没到的时候不能替人写这个 @：召唤与否是浏览器按**能不能把名字解析成
// handle** 算出来的，此刻解析不出来，写进去的 @ 只是一行字，消息照发、它照样不
// 动。所以这两个入口在那一瞬间是关着的（见 `summonReady`），宁可少一个入口，
// 也不要一个点了不算数的入口。
function withAgentMention(text: string): string {
  const agent = props.agentSeat
  if (!agent || mentionsAgent(expandMentions(text))) return text
  return `@${agent.label} ${text}`
}

// 「交给芝士」这颗按钮：它不改任何隐藏状态，它只是替你打那五个字，写完你看得见、
// 也能自己删掉。
function toggleSummon() {
  const agent = props.agentSeat
  if (!summonOn.value) {
    draft.value = withAgentMention(draft.value)
  } else if (agent) {
    // 只摘掉第一处。正文里别处还提着它（「照 @芝士 说的改」）是在说事，不是在
    // 叫它，取消这一次召唤不该顺手把那句话也改了。
    for (const pat of [`@${agent.label}`, `@${agent.handle}`]) {
      const at = draft.value.indexOf(pat)
      if (at < 0) continue
      const after = at + pat.length
      draft.value = draft.value.slice(0, at) + draft.value.slice(draft.value[after] === ' ' ? after + 1 : after)
      break
    }
  }
  void nextTick(() => composerInput.value?.focus?.())
}
let composing = false
let compositionEndedAt = -1e9
function onCompositionStart() {
  composing = true
}
function onCompositionEnd(e: CompositionEvent) {
  composing = false
  compositionEndedAt = e.timeStamp
}
function isImeKey(e: KeyboardEvent) {
  return composing || e.isComposing || e.keyCode === 229 || e.timeStamp - compositionEndedAt < 100
}

// 触摸屏上回车是换行。软键盘没有 Shift 这一层，所以「Enter 发送 / Shift+Enter
// 换行」在手机上等于「打不出第二行」——发送有按钮，换行没有别的办法。
// 按输入方式判断，不按视口宽度：带触摸屏的笔记本两样都对。
const coarse = typeof window !== 'undefined' ? window.matchMedia?.('(hover: none)') : undefined
const enterSends = ref(!coarse?.matches)
coarse?.addEventListener?.('change', (e: MediaQueryListEvent) => (enterSends.value = !e.matches))

function onComposerKey(e: KeyboardEvent) {
  // 翻进资料库之后，Esc 是退回一级的那一步（而不是把整个菜单关掉——@ 还在正文里）。
  if (e.key === 'Escape' && mentionLevel.value === 'library') {
    e.preventDefault()
    mentionLevel.value = 'root'
    return
  }
  if (e.key !== 'Enter' || e.shiftKey) return
  // IME composition (拼音选字/上屏) 的回车是按给输入法的，绝不当成发送。
  if (isImeKey(e)) return
  // Only act on Enter from the focused composer textarea itself.
  const t = e.target as HTMLElement | null
  if (!t || t.tagName !== 'TEXTAREA' || document.activeElement !== t) return
  // ⌘/Ctrl+Enter = 发送并交给芝士，正文里一个 @ 都不用打。判断排在 @-菜单前面：
  // 打到一半的 @ 不该把这个已经说清楚的「交给它」变成一次选人。
  if ((e.metaKey || e.ctrlKey) && summonReady.value) {
    e.preventDefault()
    sendDraft({ summon: true })
    return
  }
  // While the @-menu is open, Enter picks the first match instead of sending.
  if (mentionMatches.value.length) {
    e.preventDefault()
    pickMention(mentionMatches.value[0])
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
function sendDraft(opts?: { summon?: boolean }) {
  if (props.attsUploading) return
  if (!draft.value.trim() && !props.atts.length) return
  const content = expandMentions(opts?.summon ? withAgentMention(draft.value) : draft.value)
  emit('send', { content, summon: props.alwaysSummon || mentionsAgent(content) })
}

defineExpose({
  /** @ 完人、点完按钮、起手草稿写完之后要把光标还回来——这是最烦人的一处。 */
  focus() {
    void nextTick(() => composerInput.value?.focus?.())
  },
})
</script>

<template>
  <div
    class="composer pa-2 px-3"
    :class="{ 'composer--drop': dragOver }"
    @dragenter.prevent="onDragOverFiles"
    @dragover.prevent="onDragOverFiles"
    @dragleave="onDragLeaveFiles"
    @drop.prevent="onDropFiles"
  >
    <!-- @-autocomplete: 没打字是「人 / 群播 / 资料库」这一级，打了字就是搜索。 -->
    <div v-if="mentionMatches.length || mentionLevel === 'library'" class="mention-menu">
      <div v-if="mentionLevel === 'library'" class="mention-menu-head" title="按 Esc 返回">
        <v-icon size="13">mdi-folder-outline</v-icon>
        <span class="mention-menu-name">资料库</span>
      </div>
      <template v-for="(mm, i) in mentionMatches" :key="mm.kind + mm.insert">
        <div v-if="mm.group && mm.group !== mentionMatches[i - 1]?.group" class="mention-menu-group">
          {{ mm.group }}
        </div>
        <button type="button" class="mention-menu-item" @click="pickMention(mm)">
          <span v-if="mm.kind === 'broadcast'" class="mention-avatar mention-avatar--broadcast">
            <v-icon size="13">mdi-bullhorn-outline</v-icon>
          </span>
          <span
            v-else-if="mm.kind === 'member'"
            class="mention-avatar"
            :class="{ 'mention-avatar--agent': mm.agent }"
            >{{ mm.label.slice(0, 1).toUpperCase() }}</span
          >
          <span v-else-if="mm.kind === 'category'" class="mention-avatar mention-avatar--file">
            <v-icon size="13">mdi-folder-outline</v-icon>
          </span>
          <span v-else-if="mm.kind === 'file'" class="mention-avatar mention-avatar--file">
            <v-icon size="13">mdi-file-outline</v-icon>
          </span>
          <span v-else class="mention-avatar mention-avatar--topic">
            <v-icon size="13">mdi-pound</v-icon>
          </span>
          <span class="mention-menu-name">{{ mm.label }}</span>
          <span v-if="mm.agent" class="mention-agent-badge">AI 队友</span>
          <ExternalTag v-else-if="mm.external" />
          <span class="mention-menu-sub">{{ mm.sub }}</span>
          <span v-if="mm.kind === 'category'" class="mention-menu-hint">›</span>
          <span v-else-if="i === 0" class="mention-menu-hint">Enter</span>
        </button>
      </template>
      <div v-if="mentionLevel === 'library' && !mentionMatches.length" class="mention-menu-group">暂无匹配的文件</div>
    </div>
    <!-- 输入区是一个控件，不是浮在页面上的几个零件：一个圆角描边的盒子把
             「待发的图片 + 输入框 + 动作」框成一块。盒子自己就是和时间线之间的
             分隔，所以上面那条 divider 没了。 -->
    <div class="composer-box">
      <!-- 这条消息带着的东西：回复的那条在最前，后面是待发的附件。一行排开，
           不换行——多了就在这一行里横着滚，每一个都还拿得掉。 -->
      <div v-if="replyLabel || atts.length" class="chip-list">
        <ComposerChip
          v-if="replyLabel"
          key="reply"
          class="reply-chip"
          quiet
          :label="replyLabel"
          :remove-label="t('work.room.composer.cancelReply')"
          @remove="emit('clear-reply')"
        >
          <template #face><v-icon size="13">mdi-reply</v-icon></template>
        </ComposerChip>
        <AttachmentChip
          v-for="(a, i) in atts"
          :key="a.path"
          :topic-id="topic!.id"
          :attachment="a"
          @remove="emit('remove-att', i)"
        />
      </div>
      <!-- 输入框独占一整行。它旁边并排放按钮时，真正能打字的那块在手机上只剩
             半屏——而按钮的数量只会往上加。 -->
      <v-textarea
        ref="composerInput"
        v-model="draft"
        autocomplete="off"
        variant="plain"
        rows="1"
        auto-grow
        max-rows="6"
        hide-details
        density="comfortable"
        class="composer-input"
        :placeholder="hint"
        :title="
          enterSends
            ? `Enter 发送，Shift+Enter 换行，⌘/Ctrl+Enter 发送并交给${agentName}，可直接粘贴图片`
            : '可直接粘贴图片'
        "
        @keydown="onComposerKey"
        @paste="emit('paste', $event)"
        @compositionstart="onCompositionStart"
        @compositionend="onCompositionEnd"
      />
      <!-- 下面一行：动作靠左，发送靠右。发送是这一行唯一的主操作，所以它是
             唯一的实心按钮，其余一律是安静的图标。 -->
      <div class="composer-actions d-flex align-center ga-1">
        <!-- 这两个 input 是藏起来的，但**不能**用 display:none / visibility:hidden：
                 iOS Safari 拒绝用脚本打开一个被隐藏掉的文件选择框，按钮点下去
                 毫无反应。所以按 .visually-hidden 的老办法藏——留在布局里、只是
                 看不见。旁边 components/common/FileSelect.vue 里也是这么藏的。 -->
        <input ref="fileInput" type="file" multiple class="visually-hidden" @change="onFilePicked" />
        <input ref="imageInput" type="file" accept="image/*" multiple class="visually-hidden" @change="onFilePicked" />
        <!-- 附件上传走的是 HTTP，和聊天那条 socket 是两回事：socket 断着的
               时候图片照样传得上去，所以这里不跟着 `connected` 一起禁用。 -->
        <v-btn
          class="composer-icon"
          icon="mdi-paperclip"
          variant="text"
          size="small"
          color="medium-emphasis"
          title="上传文件（每个最大 10MB）"
          @click="pickFiles"
        />
        <!-- 手机上多一颗「照片」：那儿没有截图可贴、也没有东西可拖，从文件
                 选择器里翻相册要绕好几步。 -->
        <v-btn
          v-if="!mdAndUp"
          class="composer-icon"
          icon="mdi-image-outline"
          variant="text"
          size="small"
          color="medium-emphasis"
          title="发送照片"
          @click="pickImages"
        />
        <v-spacer />
        <!-- 算力说的是「这条消息会在哪儿跑」，属于发送这一侧，不和左边那两个
               「这条消息本身」的动作并列。它是设置不是动作，所以最安静。 -->
        <slot name="composer-chips" />
        <!-- 「交给芝士」：它不是一个自己存着状态的开关，它是正文的镜子——
               点一下把 @ 写进输入框（你看得见、也能自己删），手打 @ 它就自己
               亮。一个能和正文说不一样的话的开关（亮着、正文里却没有 @），会
               让「这条到底算不算叫了它」变成没人答得上来的问题。 -->
        <button
          v-if="!alwaysSummon"
          type="button"
          class="summon-btn"
          :class="{ 'summon-btn--on': summonOn }"
          :disabled="!summonReady"
          :aria-pressed="summonOn"
          :title="summonOn ? `已 @${agentName}，点击取消` : `发送并交给${agentName}（⌘/Ctrl+Enter）`"
          @click="toggleSummon"
        >
          <v-icon size="14">mdi-at</v-icon>
          <span class="summon-btn-label">交给{{ agentName }}</span>
        </button>
        <!-- 断线时照样能发：消息进发件箱、立刻显示，连上就自己走 (§14.1)。
               按 `connected` 禁用会把「打字」和「后端此刻在不在」绑在一起。 -->
        <v-btn
          class="composer-send"
          color="primary"
          variant="flat"
          icon="mdi-send"
          size="small"
          title="发送"
          :disabled="attsUploading || (!draft.trim() && !atts.length)"
          @click="sendDraft()"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.composer {
  /* @ 菜单按它定位（见 .mention-menu）。 */
  position: relative;
  background: var(--surface);
  /* 手机底部那一条圆角/横杠区（安全区）会压在输入框上。桌面上这个值是 0。 */
  padding-bottom: calc(8px + env(safe-area-inset-bottom));
}
/* 输入区是一个控件。原来输入框和几颗按钮各自浮在页面上，读起来是几个零件而不是
   一件东西——一个圆角描边就把它们收成一块，顺带替掉了上面那条 divider。 */
.composer-box {
  padding: 4px 6px 4px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  transition:
    border-color 0.12s ease,
    box-shadow 0.12s ease,
    background-color 0.12s ease;
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
.composer--drop .composer-box {
  border-color: var(--muted);
  box-shadow: inset 0 0 0 1px var(--muted);
  background: var(--fill);
}
.composer-input :deep(textarea) {
  font-size: 14px;
  line-height: var(--lh-14);
}
/* Vuetify 给输入框留的顶部内边距是「浮动标签落下来时站的地方」：plain + comfortable
   下是 15px 的 --v-input-padding-top 再加 3.5px，而底部只有 3px。这个输入框没有
   标签，那 15px 就只剩一个效果——第一行字被顶到盒子中间，上下差了六倍。 */
.composer-input :deep(.v-field) {
  --v-input-padding-top: 0px;
}
/* 那道遮罩是给「文字从浮动标签底下滑过去」用的渐隐。没有标签就没有要遮的东西，
   留着它只会把第一行字的上半截淡掉——尤其在内边距刚被收窄之后。 */
.composer-input :deep(.v-field__input) {
  -webkit-mask-image: none;
  mask-image: none;
}
/* 动作行的规矩，三条：
   1. 一行一个高度。原来是 24 / 32 / 24 / 30 四种，这是它看起来像一堆零件的主因。
   2. 静止时谁也不画边框、不画底色——状态用墨色说，不用盒子说。
   3. 整行只有一个实心块，就是发送。
   位置负责表达语义：左边是「这条消息本身」的动作，右边是「它会怎么发出去」。 */
.composer-actions {
  min-height: 28px;
  margin-top: 2px;
}
.composer-icon,
.composer-send {
  width: 28px;
  height: 28px;
}
/* 「交给芝士」。它和发送并排，但绝不能也是实心琥珀——一行里只有一个实心块，
   那个位置是发送的。亮起来只改一条描边和墨色，形态不变。 */
.summon-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 28px;
  padding: 0 10px;
  border: 1px solid transparent;
  border-radius: var(--radius-pill);
  font-size: 13px;
  line-height: 1;
  color: var(--muted);
  cursor: pointer;
  transition:
    color 0.12s ease,
    border-color 0.12s ease,
    background-color 0.12s ease;
}
.summon-btn:hover:not(:disabled) {
  background: var(--fill);
  color: var(--text);
}
.summon-btn:disabled {
  cursor: default;
  opacity: 0.5;
}
/* 开着的时候要一眼认得出：这条消息会真的开出一轮，和「只是说了句话」是两回事。
   描边那一档太轻了——它和没开的状态只差一条 1px 的线，而这一行右边还站着一颗实心
   的发送按钮，线根本抢不到注意力。所以开态是填充的：比 hover 深一档的底、墨色
   加粗的字。不用琥珀——琥珀是旁边那颗发送按钮的，两个琥珀的东西并排，人就分不
   出该点哪个。 */
.summon-btn--on {
  border-color: transparent;
  background: var(--line-2);
  color: var(--ink);
  font-weight: 600;
}
.summon-btn--on:hover:not(:disabled) {
  background: var(--line-2);
  color: var(--ink);
}
/* 窄屏上只留那个 @ 图标：这一行右边还站着算力和发送，三个都带字就换行了。 */
@media (max-width: 480px) {
  .summon-btn-label {
    display: none;
  }
}

/* @-autocomplete popup — mirrors TopicView's composer picker. */
/* 浮在输入区上方，不占位置。它原来是输入区里的一个普通块：菜单一出现输入区就长高，
   贴在输入框上面的验收横条被整条顶上去，菜单一收又掉回来。浮层本来就该带投影、
   盖在别的东西上面。 */
.mention-menu {
  position: absolute;
  right: 12px;
  bottom: 100%;
  left: 12px;
  z-index: 5;
  display: flex;
  flex-direction: column;
  margin-bottom: 4px;
  border: 1px solid var(--line-2);
  border-radius: 8px;
  /* 横向仍旧裁边（圆角靠它），纵向改自滚：规范只在某一侧是 `visible` 时才把另一
     侧算成 `auto`，所以这两条不冲突。 */
  overflow-x: hidden;
  overflow-y: auto;
  /* 菜单最多 7 项（`mentionMatches` 里 slice(0, 7)），每项 min-height 36px，展开
     就是 254px；而 `.composer` 是 `.chat`（flex column）里不肯收缩的那一项。面板
     一矮（尤其手机上），多出来的部分连同输入框一起从 `.chat` 底部溢出、被外壳裁
     掉，还没有滚动条。给个上限让它自己滚——40vh 与 `.panel-card__block-body` 同例。 */
  max-height: 40vh;
  background: var(--surface);
  box-shadow: var(--shadow-2);
}
.mention-menu-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 12px;
  min-height: 36px;
  text-align: left;
  font-size: 13px;
  cursor: pointer;
}
.mention-menu-item:hover {
  background: var(--fill);
}
.mention-avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  font-size: 12px;
  font-weight: 700;
  /* Theme-invariant pair (same call as the default avatar in LeftAppRail): the
     slate disc is one value in both themes, so its ink must be too. */
  color: #fff;
  background: #8a94a3;
  flex: none;
}
/* AI 队友在 @ 菜单里和在对话里一个样子（CheeseAvatar）：反色的圆角方块。它原来
   是一颗琥珀圆——琥珀留给主操作，不给头像。 */
.mention-avatar--agent {
  color: var(--inverse-ink);
  background: var(--inverse-surface);
  border-radius: var(--radius-sm);
}
.mention-avatar--broadcast {
  /* --ink inverts with the theme (near-black → near-white), so the ink on it
     has to invert too; --surface is #fff in light (unchanged) and #1B1D20 dark. */
  color: var(--surface);
  background: var(--ink);
}
.mention-avatar--topic,
.mention-avatar--file {
  background: var(--fill);
  color: var(--muted);
}
/* 二级菜单的头，和它里面的分组标题：两条都不是可选项，所以不长得像可选项。 */
.mention-menu-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 12px;
  border-bottom: 1px solid var(--line-2);
  color: var(--muted);
}
.mention-menu-group {
  padding: 6px 12px 2px;
  font-size: 12px;
  color: var(--faint);
}
.mention-menu-name {
  font-weight: 500;
}
.mention-agent-badge {
  font-size: 12px;
  font-weight: 600;
  padding: 0 5px;
  border-radius: var(--radius-sm);
  color: var(--muted);
  background: var(--fill);
}
.mention-menu-sub {
  font-size: 12px;
  color: var(--faint);
}
.mention-menu-hint {
  margin-left: auto;
  font-size: 12px;
  color: var(--faint);
}

/* 回复和附件那一行。不换行：换了行输入框就被一截一截往上顶。多出来的横着滚——
   裁掉的话，第四个附件既看不见也拿不掉。 */
.chip-list {
  display: flex;
  gap: 6px;
  padding: 2px 0 4px;
  overflow-x: auto;
  scrollbar-width: thin;
}
</style>
