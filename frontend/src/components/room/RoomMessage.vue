<script setup lang="ts">
// 一条消息：谁说的、什么时候、说了什么，以及能对它做什么。
//
// 人和芝士共用同一种形态——平铺，靠左，只有名字的字重不同。这是 #1447 定的：
// 这一栏里最长的一半内容是芝士的产出（markdown、diff、几十行），气泡装不下，
// 而「一对一时两种长相就是两个角色，三个人一混就没规律」。
//
// 它不认识名册，也不认识时间线：显示名、头像、时间、被回复的那一条，都是房间
// 算好传进来的。它自己只回答「这一块该画成什么」。
import type { Block } from '../../cx_types'

import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { artifactKind, artifactName, askAnswered, askOptions, isImageBlock, replySnippet } from '../../lib/blockDisplay'
import { fileIcon } from '../../lib/fileKind'
import { renderMarkdown as renderMarkdownWith, renderPlain as renderPlainWith } from '../../lib/renderMessage'
import { avatarColor, avatarInitial } from '../../utils/avatar'
import AttachmentImage from '../AttachmentImage.vue'
import CheeseAvatar from '../CheeseAvatar.vue'
import ExternalTag from '../common/ExternalTag.vue'

import RollingNumber from './RollingNumber.vue'

import { t } from '@/i18n'

/** MVP 表情选择器里那八个：常用的就够了，多了是一面墙。 */
const QUICK_EMOJIS = ['👍', '✅', '❤️', '😂', '🎉', '👀', '🙏', '➕']

const props = defineProps<{
  block: Block
  /** 被回复的那一条；没有就是 null（AI 的 reply_to 是隐式的，不画引用条）。 */
  parent: Block | null
  parentName: string | null
  /** 同一个人连着说的第一条——只有它带头像和名字。 */
  runStart: boolean
  /** 同一个人隔了一阵又开口：重新带上名字和时间，但只空一小档。 */
  regroup?: boolean
  /** 这条是我自己说的（名字加重）。 */
  mine: boolean
  topicId: string | null
  authorName: string
  /** 真头像的地址；取不到就画按 handle 哈希的色块。 */
  avatar: string | null
  isAgent: boolean
  /** 说话的人是这个项目的外部成员（团队以外、被邀请进来的）——名字旁挂「外部」。 */
  external?: boolean
  time: string
  /** handle→昵称 / 话题 id→标题，正文里的 token 靠它渲染成可点的 chip。 */
  refs: { mentionNames: Record<string, string>; topicTitles: Record<string, string> }
  /** 自己的 handle，用来标出哪些表情是自己点的。 */
  viewer: string
  pickerOpen: boolean
  askBusy: boolean
  /**
   * 这条没叫芝士、而它是最后一条——传队友的名字表示要显示那行补救提示，null 表示不用。
   */
  summonHint: string | null
  summonBusy: boolean
  /**
   * 这一条还没落库——已经在屏幕上，正在（或没能）送出去。淡一档，形状不变：
   * 它就是那条消息，不是另一种东西。`time` 那一格这时装的是送达状态。
   * 没送出去的那条带「重试」和「编辑」：编辑把原文放回输入框。
   */
  outgoing?: { error?: string; failed: boolean } | null
}>()

const emit = defineEmits<{
  (e: 'open-file', path: string, taskId: string | null): void
  (e: 'open-topic', id: string): void
  (e: 'upgrade', blockId: string): void
  (e: 'reply', block: Block): void
  (e: 'react', block: Block, emoji: string): void
  (e: 'toggle-picker', blockId: string): void
  (e: 'answer', block: Block, option: string): void
  (e: 'download', block: Block): void
  /** 跳到被回复的那一条。 */
  (e: 'jump', blockId: string): void
  (e: 'summon'): void
  (e: 'avatar-error', handle: string): void
  (e: 'retry'): void
  (e: 'edit'): void
}>()

function renderMarkdown(text: string): string {
  return renderMarkdownWith(text, props.refs)
}

function renderPlain(text: string): string {
  return renderPlainWith(text, props.refs)
}

// 芝士的回复里每个代码块右上角一颗「复制」。按钮是渲染之后加上去的，文字取自
// 这一处自己的文案，不来自消息内容，所以不必再过一遍净化。
const agentHtml = computed(() =>
  renderMarkdown(props.block.content)
    .replaceAll(
      '<pre>',
      `<div class="md-pre"><button type="button" class="md-copy">${t('work.room.message.copy')}</button><pre>`
    )
    .replaceAll('</pre>', '</pre></div>')
)

// 复制之后原地说一声「已复制」，一会儿再换回来。
const COPIED_MS = 1500
const copied = ref(false)
let copiedTimer: ReturnType<typeof setTimeout> | undefined
onBeforeUnmount(() => clearTimeout(copiedTimer))

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}

// 整条消息复制成什么：芝士的回复复制 markdown 原文（代码块、列表贴到别处还是那个
// 样子）；人说的话复制屏幕上读到的字（@ 的是名字，不是 handle）。
const textEl = ref<HTMLElement | null>(null)
async function copyMessage() {
  const text = props.isAgent ? props.block.content : textEl.value?.textContent ?? props.block.content
  if (!(await copyText(text))) return
  copied.value = true
  clearTimeout(copiedTimer)
  copiedTimer = setTimeout(() => (copied.value = false), COPIED_MS)
}

// 选项作答：点下去的那一项先变实、其余淡下去，等答案落库再换成「谁选了什么」。
// 请求没成（askBusy 落回去了、也没有答案）就松手，几个选项回到原样。
const picked = ref<string | null>(null)
function pick(option: string) {
  picked.value = option
  emit('answer', props.block, option)
}
watch(
  () => props.askBusy,
  (busy) => {
    if (!busy && !askAnswered(props.block)) picked.value = null
  }
)

async function onAgentTextClick(e: MouseEvent) {
  const btn = (e.target as HTMLElement | null)?.closest('.md-copy') as HTMLButtonElement | null
  const code = btn?.parentElement?.querySelector('pre')
  if (!btn || !code || !(await copyText(code.textContent ?? ''))) return
  btn.textContent = t('work.room.message.copied')
  setTimeout(() => (btn.textContent = t('work.room.message.copy')), COPIED_MS)
}
</script>

<template>
  <div
    class="im-row"
    :class="{
      'im-row--cont': !runStart,
      'im-row--regroup': runStart && regroup,
      'im-row--self': mine,
      'im-row--pending': !!outgoing,
    }"
    :data-mid="block.id"
  >
    <!-- avatar gutter: only on the first of a run -->
    <div class="im-gutter">
      <template v-if="runStart">
        <CheeseAvatar v-if="isAgent" :size="28" :name="authorName" />
        <!-- 真头像；取不到或加载失败退回按 handle 哈希的彩色首字母。
           底色的种子继续用 handle（换成昵称会让每个人的颜色都变）,
           变的只有色块里的字。 -->
        <img
          v-else-if="avatar"
          class="im-avatar im-avatar--photo"
          :src="avatar"
          :alt="authorName"
          @error="emit('avatar-error', block.author)"
        />
        <div v-else class="im-avatar" :style="{ backgroundColor: avatarColor(block.author) }">
          {{ avatarInitial(authorName) }}
        </div>
      </template>
      <!-- 续话没有名字那一行，时间在悬停时出现在头像列里，和正文第一行对齐。 -->
      <span v-else-if="!outgoing" class="im-gutter-time">{{ time }}</span>
    </div>
    <!-- 还没送出去的续话同样没有名字那一行，送达状态（发送中 / 等待连接）就挂在
       行尾，一直在，不等悬停：断网时人要知道这几句为什么是淡的。 -->
    <span v-if="outgoing && !runStart && time" class="im-pending-state">{{ time }}</span>

    <div class="im-main">
      <div v-if="runStart" class="im-meta">
        <span class="im-name">{{ authorName }}</span>
        <ExternalTag v-if="external && !isAgent" />
        <span class="im-time">{{ time }}</span>
      </div>
      <!-- B3: a reply shows the message it threads under -->
      <button v-if="parent" type="button" class="im-replied" @click="emit('jump', parent.id)">
        <v-icon size="12">mdi-reply</v-icon>
        回复 {{ parentName }}：{{ replySnippet(parent) }}
      </button>
      <!-- 图片输入: an attachment block renders as the image itself
         (click opens the original in a new tab). 字节在 AttachmentImage
         里取——raw 端点只认 Authorization 头，裸挂 URL 是匿名请求。 -->
      <AttachmentImage v-if="isImageBlock(block)" :topic-id="topicId" :path="block.content" />
      <v-btn
        v-else-if="block.kind === 'attachment'"
        variant="text"
        prepend-icon="mdi-file-document-outline"
        append-icon="mdi-download-outline"
        class="text-none im-file-link"
        :title="`下载 ${artifactName(block)}`"
        @click="emit('download', block)"
      >
        <span class="text-truncate">{{ artifactName(block) }}</span>
      </v-btn>
      <!-- 芝士摆出来给人看的一份东西（`cheese show`）。后端一直在往时间线
         写这样一块（kind=artifact，content 是路径），而这里一直没有认它的
         分支，于是它掉进最下面那个兜底里，渲染成一行光秃秃的文件名——
         和芝士随口说了个路径长得一模一样。
         点它交给拿着面板的那一层去开，走的是 <&path> 芯片同一条线。 -->
      <button
        v-else-if="block.kind === 'artifact'"
        type="button"
        class="im-artifact"
        :title="`打开 ${artifactName(block)}`"
        @click="emit('open-file', block.content, block.task_id ?? null)"
      >
        <span class="att-face im-artifact__face">
          <v-icon size="20">{{ fileIcon(block.content) }}</v-icon>
        </span>
        <span class="im-artifact__text">
          <span class="im-artifact__name">{{ artifactName(block) }}</span>
          <span class="im-artifact__kind t-meta">{{ artifactKind(block) }}</span>
        </span>
        <v-icon size="16" class="im-artifact__go">mdi-arrow-top-right</v-icon>
      </button>
      <div v-else-if="isAgent" class="im-text md-content" @click="onAgentTextClick" v-html="agentHtml" />
      <!-- 现场尊重原文: human text renders verbatim — newlines and
         spacing preserved (pre-wrap), no markdown reflow. -->
      <div v-else ref="textEl" class="im-text im-text--verbatim" v-html="renderPlain(block.content)" />
      <div v-if="outgoing?.failed" class="outbox-fail" role="alert">
        <span class="outbox-fail__text">{{
          outgoing.error ? t('work.room.outbox.failed', { reason: outgoing.error }) : t('work.room.outbox.undelivered')
        }}</span>
        <button type="button" class="outbox-btn" @click="emit('retry')">{{ t('work.room.retry.action') }}</button>
        <button type="button" class="outbox-btn" :title="t('work.room.outbox.editHint')" @click="emit('edit')">
          {{ t('work.room.outbox.edit') }}
        </button>
      </div>
      <!-- 选项问题 (cheese_ask): one-click answer buttons; answered
         state shows the pick + who made it (everyone sees it). -->
      <Transition name="ask-swap" mode="out-in">
        <div v-if="askOptions(block) && !askAnswered(block)" key="options" class="ask-row">
          <button
            v-for="opt in askOptions(block)!"
            :key="opt"
            type="button"
            class="ask-option"
            :class="{ 'ask-option--picked': picked === opt, 'ask-option--dim': picked !== null && picked !== opt }"
            :disabled="askBusy || picked !== null"
            @click="pick(opt)"
          >
            {{ opt }}
          </button>
        </div>
        <div v-else-if="askOptions(block)" key="answered" class="ask-row">
          <div class="ask-answered">
            <v-icon size="13" class="c-ok">mdi-check-circle</v-icon>
            {{ askAnswered(block)!.by }} 选了「{{ askAnswered(block)!.option }}」
          </div>
        </div>
      </Transition>
      <!-- 活引用 (eval A1): 升级出去的块指向它变成的那个地点。房间里
         升级出来的是一条支线，私聊里升级出来的才是房间——两个字段各指
         一张表，同时只会有一个非空。 -->
      <button
        v-if="block.upgraded_to_task_id || block.upgraded_to_topic_id"
        type="button"
        class="im-upgraded"
        @click="emit('open-topic', (block.upgraded_to_task_id || block.upgraded_to_topic_id)!)"
      >
        <v-icon size="13">mdi-arrow-top-right</v-icon>
        已转为话题
      </button>
      <!-- 忘了 @ 的补救：房间里最后一句是对着人说的，芝士就不会动，
         而在这一行出现之前，房间里没有任何东西说明这一点。 -->
      <div v-if="summonHint" class="summon-hint">
        <span class="summon-hint-text">未交给{{ summonHint }}</span>
        <button type="button" class="summon-hint-btn" :disabled="summonBusy" @click="emit('summon')">
          交给{{ summonHint }}
        </button>
      </div>
      <!-- Emoji reaction chips (Slack): count per emoji, own reactions
         highlighted; click toggles. 芝士's 👀 receipt lands here too. -->
      <TransitionGroup v-if="block.reactions?.length" tag="div" name="rx" class="rx-row">
        <button
          v-for="r in block.reactions"
          :key="r.emoji"
          type="button"
          class="rx-chip"
          :class="{ 'rx-chip--mine': r.authors.includes(viewer) }"
          :title="r.authors.join('、')"
          @click="emit('react', block, r.emoji)"
        >
          <span class="rx-emoji">{{ r.emoji }}</span>
          <RollingNumber class="rx-count" :value="r.count" />
        </button>
      </TransitionGroup>
    </div>

    <!-- hover action bar, top-right of the row (Feishu). Only actions
       we actually implement are shown (no dead buttons). 还没落库的那条没有:
       回复和升级都要一个库里的 id，而表情要一条别人也看得见的消息。 -->
    <div v-if="!outgoing" class="im-actions" :class="{ 'im-actions--open': pickerOpen }">
      <button
        type="button"
        class="im-act rx-toggle"
        :class="{ 'im-act--on': pickerOpen }"
        title="添加表情"
        @click="emit('toggle-picker', block.id)"
      >
        <v-icon size="15">mdi-emoticon-happy-outline</v-icon>
      </button>
      <button type="button" class="im-act" title="回复" @click="emit('reply', block)">
        <v-icon size="15">mdi-reply-outline</v-icon>
      </button>
      <button
        type="button"
        class="im-act"
        :title="copied ? t('work.room.message.copied') : t('work.room.message.copy')"
        @click="copyMessage"
      >
        <v-icon size="15">{{ copied ? 'mdi-check' : 'mdi-content-copy' }}</v-icon>
      </button>
      <button type="button" class="im-act" title="转为话题" @click="emit('upgrade', block.id)">
        <v-icon size="15">mdi-comment-arrow-right-outline</v-icon>
      </button>
      <!-- MVP emoji picker: the 8 common reactions, Slack-style. -->
      <Transition name="rx-picker">
        <div v-if="pickerOpen" class="rx-picker">
          <button v-for="e in QUICK_EMOJIS" :key="e" type="button" class="rx-pick" @click="emit('react', block, e)">
            {{ e }}
          </button>
        </div>
      </Transition>
    </div>
  </div>
</template>

<style scoped src="./room-row.css"></style>

<style scoped>
/* 忘了 @ 的补救行。它属于那条消息（和正文左对齐），不是一条平台行——平台行说的
   是平台做了什么，这一行说的是**你**还差一步。 */
.summon-hint {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
  font-size: 13px;
  color: var(--faint);
}
.summon-hint-btn {
  padding: 2px 8px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
  color: var(--muted);
  cursor: pointer;
}
.summon-hint-btn:hover:not(:disabled) {
  border-color: var(--faint);
  color: var(--ink);
}
.summon-hint-btn:disabled {
  cursor: default;
  opacity: 0.6;
}

/* B3: the "回复 X：…" cue above a reply, and the composer reply-to bar. */
.im-replied {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  max-width: 100%;
  margin-bottom: 3px;
  padding: 1px 6px;
  font-size: 12px;
  color: var(--muted);
  background: var(--fill);
  border-radius: var(--radius-sm);
  cursor: pointer;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.im-replied:hover {
  color: var(--ink);
}
.im-pending-state {
  position: absolute;
  top: 4px;
  right: 16px;
  font-size: 12px;
  line-height: var(--lh-14-loose);
  color: var(--faint);
}
/* 发件箱: 已显示、还没落库。淡一档，不换形状——它就是那条消息。 */
.im-row--pending .im-text,
.im-row--pending .im-name {
  opacity: 0.62;
}
/* 没送出去：一句为什么，后面两颗和事件行同一种中性小按钮。 */
.outbox-fail {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 8px;
  margin-top: 6px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--danger-ink);
}
.outbox-fail__text {
  overflow-wrap: anywhere;
}
.outbox-btn {
  flex: none;
  height: 24px;
  padding: 0 8px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
  background: var(--surface);
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
  cursor: pointer;
  transition:
    background-color var(--dur-quick) var(--ease-standard),
    border-color var(--dur-quick) var(--ease-standard);
}
.outbox-btn:hover {
  background: var(--fill);
  border-color: var(--faint);
}
/* 现场尊重原文: exactly what the human typed, line breaks included. */
.im-text--verbatim {
  white-space: pre-wrap;
}
/* 芝士摆出来的一份东西。正文平铺之后，这一栏里描边的块只剩它——所以那道边就是
   「这不是一句话，是一个可以打开的东西」。 */
.im-artifact {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  max-width: 100%;
  padding: 8px 12px 8px 8px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  background: var(--surface);
  text-align: left;
  cursor: pointer;
  transition:
    background-color var(--dur-quick) var(--ease-standard),
    border-color var(--dur-quick) var(--ease-standard);
}
.im-artifact:hover {
  background: var(--fill);
  border-color: var(--faint);
}
.im-artifact__face {
  width: 32px;
  height: 32px;
}
.im-artifact__text {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}
.im-artifact__name {
  font-size: 13px;
  line-height: var(--lh-13);
  font-weight: 600;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.im-artifact__kind {
  text-align: left;
}
.im-artifact__go {
  flex: none;
  color: var(--faint);
}
.im-file-link {
  max-width: 100%;
}
.im-file-link :deep(.v-btn__content) {
  min-width: 0;
}
.im-upgraded {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  margin-top: 4px;
  padding: 2px 8px;
  font-size: 12px;
  color: var(--text);
  background: var(--fill);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
  cursor: pointer;
}
.im-upgraded:hover {
  background: var(--surface);
  border-color: var(--faint);
}
/* @mention: neutral inset, ink text — not amber. */
.im-text :deep(.mention) {
  color: var(--accent-ink);
  background: var(--fill);
  border-radius: var(--radius-sm);
  padding: 0 3px;
  font-weight: 500;
  cursor: pointer;
}
/* @person handle reads as a link: persistent accent underline. File/topic
   refs (file icon / #) keep their chip look and only underline on hover. */
.im-text :deep(.mention:not(.file-ref):not(.topic-ref)) {
  text-decoration: underline;
  text-underline-offset: 2px;
}
.im-text :deep(.mention:hover) {
  text-decoration: underline;
}
/* 文件 chip 前的 mdi 图标。不挂在 .im-text 下：同样的 chip 也出现在动作卡
   (.action-verb) 和系统事件行里，那两处不在 .im-text 里面。 */
:deep(.file-ref__icon) {
  margin-right: 3px;
  font-size: 0.92em;
}

/* per-row hover action bar (Feishu), floats at the row's top-right */
.im-actions {
  position: absolute;
  top: -12px;
  right: 12px;
  display: flex;
  gap: 2px;
  padding: 3px;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-1);
  opacity: 0;
  transition: opacity var(--dur-quick) var(--ease-standard);
  pointer-events: none;
}
/* One quiet square button per action: muted ink, fill on hover — the harsh
   default round icon-buttons inside a rounded pill read as unfinished. */
.im-act {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border: none;
  border-radius: var(--radius-sm);
  background: none;
  color: var(--muted);
  cursor: pointer;
  transition:
    background-color var(--dur-quick) var(--ease-standard),
    color var(--dur-quick) var(--ease-standard);
}
.im-act:hover {
  background: var(--fill);
  color: var(--ink);
}
.im-act--on {
  background: var(--line-2);
  color: var(--ink);
}
.im-row:hover .im-actions,
.im-actions--open {
  opacity: 1;
  pointer-events: auto;
}

/* ---- Emoji reactions (Slack) ---- */
/* MVP picker: a strip of the 8 common emoji, floating under the action bar.
   从右上角那颗按钮下面长出来，收回也回到那里。 */
.rx-picker-enter-active {
  transition:
    transform var(--dur-base) var(--ease-out),
    opacity var(--dur-base) var(--ease-out);
}
.rx-picker-leave-active {
  transition:
    transform var(--dur-quick) var(--ease-in),
    opacity var(--dur-quick) var(--ease-in);
}
.rx-picker-enter-from,
.rx-picker-leave-to {
  opacity: 0;
  transform: translateY(-4px) scale(0.96);
}
.rx-picker {
  transform-origin: top right;
  position: absolute;
  top: calc(100% + 4px);
  right: 0;
  display: flex;
  gap: 2px;
  padding: 4px;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-2);
  z-index: 5;
}
.rx-pick {
  width: 28px;
  height: 28px;
  border: none;
  background: none;
  border-radius: var(--radius-sm);
  /* 这个 16px 量的是一枚 emoji 字形，不是正文，所以不走字号阶梯；`line-height: 1`
     同理——它是把字形在 28px 方格里居中的手段，不是一段话的行距。 */
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
}
.rx-pick:hover {
  background: var(--fill);
}
/* 选项问题 buttons (cheese_ask): quiet outlined buttons. 悬停只加深一档，不上琥珀：
   一排选项里没有哪一个是「主操作」。 */
.ask-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
}
.ask-option {
  border: 1px solid var(--line-2);
  background: var(--surface);
  border-radius: var(--radius-md);
  padding: 5px 14px;
  font-size: 13px;
  cursor: pointer;
  transition:
    border-color var(--dur-quick) var(--ease-standard),
    background-color var(--dur-quick) var(--ease-standard),
    color var(--dur-quick) var(--ease-standard),
    opacity var(--dur-base) var(--ease-standard);
}
.ask-option:hover:not(:disabled) {
  border-color: var(--faint);
  background: var(--fill);
}
.ask-option:disabled {
  cursor: default;
}
.ask-option--picked {
  border-color: var(--muted);
  background: var(--line-2);
  color: var(--ink);
}
.ask-option--dim {
  opacity: 0.45;
}
/* 选项换成「谁选了什么」：先淡出，再淡入。 */
.ask-swap-enter-active {
  transition: opacity var(--dur-base) var(--ease-out);
}
.ask-swap-leave-active {
  transition: opacity var(--dur-quick) var(--ease-in);
}
.ask-swap-enter-from,
.ask-swap-leave-to {
  opacity: 0;
}
.ask-answered {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 13px;
  color: var(--muted);
}

/* Reaction chips under a message: emoji + count; own reactions get a darker
   outline and ground (Slack's "you reacted" affordance), not amber. */
.rx-row {
  position: relative; /* 缩掉的那颗在这一行里原地离开，不把别的撑开 */
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 4px;
}
.rx-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 22px;
  padding: 0 8px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-pill);
  background: var(--fill);
  font-size: 12px;
  line-height: 1;
  color: var(--muted);
  cursor: pointer;
  transition: border-color var(--dur-quick) var(--ease-standard);
}
.rx-chip:hover {
  border-color: var(--faint);
}
/* 表情：新的一颗从小弹到位，归零的那颗缩掉，旁边的滑过来补位。 */
.rx-enter-active {
  transition:
    transform var(--dur-base) var(--ease-out),
    opacity var(--dur-base) var(--ease-out);
}
.rx-leave-active {
  position: absolute;
  transition:
    transform var(--dur-quick) var(--ease-in),
    opacity var(--dur-quick) var(--ease-in);
}
.rx-enter-from,
.rx-leave-to {
  opacity: 0;
  transform: scale(0.6);
}
.rx-move {
  transition: transform var(--dur-base) var(--ease-standard);
}
.rx-chip--mine {
  border-color: var(--muted);
  background: var(--line-2);
  color: var(--ink);
}
.rx-emoji {
  font-size: 13px;
}
.rx-count {
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 600;
}

/* Rendered markdown for 芝士's replies (v-html → :deep). */
/* 行距和人说的话是同一档（room-row.css 的 .im-text）：同一列里两种行距，扫下来
   就是一段松一段紧。字号折到 14px 是为了让下面那几个 em 的子元素（h1/h2/h3、
   code）有一个干净的基数。 */
.md-content {
  font-size: 14px;
  line-height: var(--lh-14-loose);
}
.md-content :deep(p) {
  margin: 0 0 8px;
}
.md-content :deep(p:last-child) {
  margin-bottom: 0;
}
.md-content :deep(h1),
.md-content :deep(h2),
.md-content :deep(h3) {
  font-size: 1.02em;
  font-weight: 600;
  margin: 10px 0 4px;
}
.md-content :deep(ul),
.md-content :deep(ol) {
  margin: 4px 0;
  padding-left: 20px;
}
.md-content :deep(li) {
  margin: 2px 0;
}
.md-content :deep(li::marker) {
  color: var(--faint);
}
.md-content :deep(a) {
  color: var(--accent-ink);
  text-decoration: none;
  overflow-wrap: anywhere;
}
.md-content :deep(a:hover) {
  text-decoration: underline;
}
.md-content :deep(img) {
  max-width: 100%;
  height: auto;
  border-radius: var(--radius-md);
}
.md-content :deep(table) {
  display: block;
  width: max-content;
  max-width: 100%;
  overflow-x: auto;
}
/* 行内代码压一层 --fill 再描一道浅线：行本身就是 --surface，只描线的话它在
   悬停刷成 --fill 的那一行上会消失。 */
.md-content :deep(code) {
  font-family: var(--font-mono);
  background: var(--fill);
  border: 1px solid var(--line);
  padding: 0.5px 5px;
  border-radius: var(--radius-sm);
  font-size: 0.88em;
}
/* 代码块和它右上角的「复制」。按钮悬停时才出现；没有悬停的设备上一直在。 */
.md-content :deep(.md-pre) {
  position: relative;
}
.md-content :deep(.md-copy) {
  position: absolute;
  top: 6px;
  right: 6px;
  height: 24px;
  padding: 0 8px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
  background: var(--surface);
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
  cursor: pointer;
  opacity: 0;
  transition:
    opacity var(--dur-quick) var(--ease-standard),
    color var(--dur-quick) var(--ease-standard);
}
.md-content :deep(.md-pre:hover .md-copy),
.md-content :deep(.md-copy:focus-visible) {
  opacity: 1;
}
.md-content :deep(.md-copy:hover) {
  color: var(--ink);
}
@media (hover: none) {
  .md-content :deep(.md-copy) {
    opacity: 1;
  }
}
.md-content :deep(pre) {
  background: var(--surface);
  border: 1px solid var(--line);
  padding: 10px 12px;
  border-radius: var(--radius-md);
  overflow-x: auto;
}
/* 代码块里的 <code> 是行内元素：它身上的边框会在每一行上各画一个框。 */
.md-content :deep(pre code) {
  background: none;
  border: 0;
  padding: 0;
}
.md-content :deep(blockquote) {
  margin: 6px 0;
  padding-left: 12px;
  border-left: 2px solid var(--line-2);
  color: var(--muted);
}
</style>
