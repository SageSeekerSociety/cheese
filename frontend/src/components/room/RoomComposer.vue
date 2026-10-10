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
//
// 这一层现在只做三件事：把这几块拼在一起、处理键盘（回车到底是挑人、换行还是
// 发送）、以及把拖进来的东西交给房间（#2143）。@ 补全的状态机在
// `composables/useRoomMentionPicker.ts`，菜单、待发条和动作行各自是一件只管画的
// 东西（`MentionMenu.vue` / `ComposerChipRow.vue` / `ComposerActions.vue`）。
import type { MentionPoolEntry } from '@/composables/useRoomMentionPicker'
import type { ChatAttachment, Topic } from '../../cx_types'

import { computed, nextTick, ref } from 'vue'
import { useDisplay } from 'vuetify'
import { toast } from 'vuetify-sonner'

import { useRoomMentionPicker } from '@/composables/useRoomMentionPicker'

import { pastedTextName } from '../../lib/attachments'
import {
  expandComposerMentions,
  mentionsAgent as containsAgentMention,
  withAgentMention as addAgentMention,
} from '../../lib/composerMentions'
import { myHandle } from '../../me'

import ComposerActions from './ComposerActions.vue'
import ComposerChecklistDialog from './ComposerChecklistDialog.vue'
import ComposerChipRow from './ComposerChipRow.vue'
import MentionMenu from './MentionMenu.vue'
import ReminderDialog from './ReminderDialog.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import i18n, { t } from '@/i18n'

const props = defineProps<{
  topic: Topic | null
  /** @ 得到的人：这个房间里的，加上项目里还没进这个房间的。 */
  mentionPool: MentionPoolEntry[]
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
  /** 发一张自己的清单；不给就没有这个入口。 */
  postChecklist?: (steps: string[]) => Promise<boolean>
}>()

const checklistOpen = ref(false)

const emit = defineEmits<{
  /** 发这一条。附件由房间补上——它才知道此刻待发条里有什么。 */
  (e: 'send', message: { content: string; summon: boolean }): void
  (e: 'files', files: File[]): void
  (e: 'drop-files', event: DragEvent): void
  (e: 'paste', event: ClipboardEvent): void
  (e: 'remove-att', index: number): void
  /** 上传失败的那一枚按了重试：房间拿着 File 再传一次。 */
  (e: 'retry-att', index: number): void
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
  // 和原生粘贴一样：\r\n 归一成 \n（Windows 上复制来的文本带 \r）。
  const text = raw.replace(/\r\n?/g, '\n')
  const at = e.target as HTMLTextAreaElement | null
  const start = at?.selectionStart ?? draft.value.length
  const end = at?.selectionEnd ?? start
  draft.value = draft.value.slice(0, start) + text + draft.value.slice(end)
  longPaste.value = { start, text }
  // 光标落在刚贴进来的那一段之后，和原生粘贴的手感一致。
  void nextTick(() => {
    at?.setSelectionRange?.(start + text.length, start + text.length)
  })
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

const composerInput = ref<{ focus?: () => void } | null>(null)
const { mdAndUp } = useDisplay()
const mentionMenu = ref<InstanceType<typeof MentionMenu> | null>(null)

// @ 补全那一整套（候选、两级菜单、资料库、高亮）在 composable 里；这里只把光标
// 还给它，和把「挑中的是一份文件」转成往上发的那件事。
const picker = useRoomMentionPicker({
  draft,
  topic: () => props.topic,
  mentionPool: () => props.mentionPool,
  topicList: () => props.topicList,
  focus: () => composerInput.value?.focus?.(),
  onAddLibraryFile: (path) => emit('add-library-file', path),
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
  return addAgentMention(
    text,
    props.agentSeat,
    props.mentionPool,
    props.topicList.filter((t) => t.kind !== 'root')
  )
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
function sendDraft(opts?: { summon?: boolean }) {
  if (props.attsUploading) return
  if (!draft.value.trim() && !props.atts.length) return
  const content = expandMentions(opts?.summon ? withAgentMention(draft.value) : draft.value)
  emit('send', { content, summon: props.alwaysSummon || mentionsAgent(content) })
}

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

// 发送键亮不亮：有字，或者有东西跟着走。
const canSend = computed(() => !!draft.value.trim() || props.atts.length > 0)

defineExpose({
  /** @ 完人、点完按钮、起手草稿写完之后要把光标还回来——这是最烦人的一处。 */
  focus() {
    void nextTick(() => composerInput.value?.focus?.())
  },
})
</script>

<template>
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
    <!-- A very long plain-text paste: offer to carry it as a file instead of
         flooding the box. Inline and non-modal — ignoring it keeps the text
         exactly as pasted, which is the behavior that was here before. -->
    <div v-if="longPaste" class="long-paste">
      <span class="long-paste__text">
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
        class="long-paste__x"
        :aria-label="t('work.room.composer.longPaste.keep')"
        :title="t('work.room.composer.longPaste.keep')"
        @click="keepLongPaste"
      >
        <v-icon size="14">mdi-close</v-icon>
      </button>
    </div>
    <!-- 输入区是一个控件，不是浮在页面上的几个零件：一个圆角描边的盒子把
             「待发的图片 + 输入框 + 动作」框成一块。盒子自己就是和时间线之间的
             分隔，所以上面那条 divider 没了。 -->
    <div class="composer-box">
      <!-- 这条消息带着的东西：回复的那条在最前，后面是待发的附件。 -->
      <ComposerChipRow
        :reply-label="replyLabel"
        :atts="atts"
        :topic-id="topic?.id ?? null"
        @remove-att="(i: number) => emit('remove-att', i)"
        @retry-att="(i: number) => emit('retry-att', i)"
        @clear-reply="emit('clear-reply')"
      />
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
        :title="enterSends ? t('work.room.composer.keysHint', { name: agentName }) : t('work.room.composer.pasteHint')"
        @keydown="onComposerKey"
        @paste="onPaste"
        @compositionstart="onCompositionStart"
        @compositionend="onCompositionEnd"
      />
      <ComposerActions
        :uploading="attsUploading"
        :can-send="canSend"
        :show-image-picker="!mdAndUp"
        :enter-sends="enterSends"
        :always-summon="alwaysSummon"
        :summon-on="summonOn"
        :summon-ready="summonReady"
        :agent-name="agentName"
        :can-checklist="!!postChecklist"
        :can-remind="!!topic"
        :collapse-extras="!mdAndUp"
        @files="emit('files', $event)"
        @checklist="checklistOpen = true"
        @toggle-summon="toggleSummon"
        @remind="reminderOpen = true"
        @send="sendDraft()"
      >
        <template #chips><slot name="composer-chips" /></template>
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
/* 长文字粘贴的那条提议：贴着输入框的一行，安静的底，两个按钮。忽略它就是不管，
   所以它不抢颜色、不遮住输入框。 */
.long-paste {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  margin-bottom: 6px;
  background: var(--fill);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}
.long-paste__text {
  flex: 1 1 auto;
  min-width: 0;
}
.long-paste__x {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border: none;
  background: none;
  border-radius: var(--radius-sm);
  color: var(--faint);
  cursor: pointer;
  transition:
    background-color var(--dur-quick) var(--ease-standard),
    color var(--dur-quick) var(--ease-standard);
}
.long-paste__x:hover {
  background: var(--line-2);
  color: var(--ink);
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
.composer--drop .composer-box {
  border-color: var(--muted);
  box-shadow: inset 0 0 0 1px var(--muted);
  background: var(--fill);
}
.composer-input :deep(textarea) {
  /* 和消息流同一档（§3.2）：打出来的字和发出去以后的字一样大。 */
  font-size: 15px;
  line-height: var(--lh-15-reading);
  /* 多一行长高一行、发出去收回一行。auto-grow 的高度落在 min-height 上（Vuetify
     经 --v-input-control-height 算出来），让它过渡过去，不跳。透明度那一条是
     Vuetify 自己的，写在一起才不被盖掉。 */
  transition:
    min-height var(--dur-quick) var(--ease-standard),
    opacity var(--dur-quick) var(--ease-standard);
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
</style>
