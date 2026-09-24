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

import { artifactKind, artifactName, askAnswered, askOptions, isImageBlock, replySnippet } from '../../lib/blockDisplay'
import { fileIcon } from '../../lib/fileKind'
import { renderMarkdown as renderMarkdownWith, renderPlain as renderPlainWith } from '../../lib/renderMessage'
import { avatarColor, avatarInitial } from '../../utils/avatar'
import AttachmentImage from '../AttachmentImage.vue'
import CheeseAvatar from '../CheeseAvatar.vue'
import ExternalTag from '../common/ExternalTag.vue'

/** MVP 表情选择器里那八个：常用的就够了，多了是一面墙。 */
const QUICK_EMOJIS = ['👍', '✅', '❤️', '😂', '🎉', '👀', '🙏', '➕']

const props = defineProps<{
  block: Block
  /** 被回复的那一条；没有就是 null（AI 的 reply_to 是隐式的，不画引用条）。 */
  parent: Block | null
  parentName: string | null
  /** 同一个人连着说的第一条——只有它带头像和名字。 */
  runStart: boolean
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
  (e: 'drop'): void
}>()

function renderMarkdown(text: string): string {
  return renderMarkdownWith(text, props.refs)
}

function renderPlain(text: string): string {
  return renderPlainWith(text, props.refs)
}
</script>

<template>
  <div
    class="im-row"
    :class="{ 'im-row--cont': !runStart, 'im-row--self': mine, 'im-row--pending': !!outgoing }"
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
    </div>

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
      <div v-else-if="isAgent" class="im-text md-content" v-html="renderMarkdown(block.content)" />
      <!-- 现场尊重原文: human text renders verbatim — newlines and
         spacing preserved (pre-wrap), no markdown reflow. -->
      <div v-else class="im-text im-text--verbatim" v-html="renderPlain(block.content)" />
      <p v-if="outgoing?.error" class="outbox-error" role="alert">{{ outgoing.error }}</p>
      <div v-if="outgoing?.failed" class="outbox-actions">
        <button type="button" class="outbox-act" @click="emit('retry')">重试</button>
        <button type="button" class="outbox-act" @click="emit('drop')">删除</button>
      </div>
      <!-- 选项问题 (cheese_ask): one-click answer buttons; answered
         state shows the pick + who made it (everyone sees it). -->
      <div v-if="askOptions(block)" class="ask-row">
        <template v-if="!askAnswered(block)">
          <button
            v-for="opt in askOptions(block)!"
            :key="opt"
            type="button"
            class="ask-option"
            :disabled="askBusy"
            @click="emit('answer', block, opt)"
          >
            {{ opt }}
          </button>
        </template>
        <div v-else class="ask-answered">
          <v-icon size="13" color="primary">mdi-check-circle</v-icon>
          {{ askAnswered(block)!.by }} 选了「{{ askAnswered(block)!.option }}」
        </div>
      </div>
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
        已升级为话题，点击查看
      </button>
      <!-- 忘了 @ 的补救：房间里最后一句是对着人说的，芝士就不会动，
         而在这一行出现之前，房间里没有任何东西说明这一点。 -->
      <div v-if="summonHint" class="summon-hint">
        <span class="summon-hint-text">这条没叫{{ summonHint }}，它不会现在动</span>
        <button type="button" class="summon-hint-btn" :disabled="summonBusy" @click="emit('summon')">
          让它现在就看
        </button>
      </div>
      <!-- Emoji reaction chips (Slack): count per emoji, own reactions
         highlighted; click toggles. 芝士's 👀 receipt lands here too. -->
      <div v-if="block.reactions?.length" class="rx-row">
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
          <span class="rx-count">{{ r.count }}</span>
        </button>
      </div>
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
      <button type="button" class="im-act" title="升级为话题" @click="emit('upgrade', block.id)">
        <v-icon size="15">mdi-comment-arrow-right-outline</v-icon>
      </button>
      <!-- MVP emoji picker: the 8 common reactions, Slack-style. -->
      <div v-if="pickerOpen" class="rx-picker">
        <button v-for="e in QUICK_EMOJIS" :key="e" type="button" class="rx-pick" @click="emit('react', block, e)">
          {{ e }}
        </button>
      </div>
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
  border-color: var(--accent);
  color: var(--accent-ink);
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
  color: var(--accent-ink);
}
/* 发件箱: 已显示、还没落库。淡一档，不换形状——它就是那条消息。 */
.im-row--pending .im-text,
.im-row--pending .im-name {
  opacity: 0.62;
}
.outbox-error {
  margin: 6px 0;
  font-size: 13px;
  color: var(--danger-ink);
  overflow-wrap: anywhere;
}
.outbox-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 2px;
}
.outbox-act {
  border: none;
  background: none;
  padding: 0;
  font-size: 12px;
  color: var(--accent-ink);
  cursor: pointer;
}
.outbox-act:hover {
  text-decoration: underline;
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
    background-color 0.12s ease,
    border-color 0.12s ease;
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
  color: var(--accent-ink);
  background: var(--fill);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
  cursor: pointer;
}
.im-upgraded:hover {
  background: var(--surface);
  border-color: var(--accent);
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
  border-radius: 8px;
  box-shadow: var(--shadow-1);
  opacity: 0;
  transition: opacity 0.12s ease;
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
  border-radius: 6px;
  background: none;
  color: var(--muted);
  cursor: pointer;
  transition:
    background 0.1s ease,
    color 0.1s ease;
}
.im-act:hover {
  background: var(--fill);
  color: var(--ink);
}
.im-act--on {
  background: rgba(var(--v-theme-primary), 0.12);
  color: rgb(var(--v-theme-primary));
}
.im-row:hover .im-actions,
.im-actions--open {
  opacity: 1;
  pointer-events: auto;
}

/* ---- Emoji reactions (Slack) ---- */
/* MVP picker: a strip of the 8 common emoji, floating under the action bar. */
.rx-picker {
  position: absolute;
  top: calc(100% + 4px);
  right: 0;
  display: flex;
  gap: 2px;
  padding: 4px;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: 8px;
  box-shadow: var(--shadow-2);
  z-index: 5;
}
.rx-pick {
  width: 28px;
  height: 28px;
  border: none;
  background: none;
  border-radius: 6px;
  /* 这个 16px 量的是一枚 emoji 字形，不是正文，所以不走字号阶梯；`line-height: 1`
     同理——它是把字形在 28px 方格里居中的手段，不是一段话的行距。 */
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
}
.rx-pick:hover {
  background: var(--fill);
}
/* 选项问题 buttons (cheese_ask): quiet outlined pills, amber on hover. */
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
    border-color 0.12s,
    background 0.12s;
}
.ask-option:hover {
  border-color: rgb(var(--v-theme-primary));
  background: rgba(var(--v-theme-primary), 0.06);
}
.ask-option:disabled {
  opacity: 0.5;
  cursor: default;
}
.ask-answered {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 13px;
  color: var(--muted);
}

/* Reaction chips under a message: emoji + count; own reactions get the amber
   outline (Slack's "you reacted" affordance). */
.rx-row {
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
  transition: border-color 0.12s ease;
}
.rx-chip:hover {
  border-color: var(--accent);
}
.rx-chip--mine {
  border-color: var(--accent);
  background: var(--surface);
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
/* 渲染出来的 markdown 走 style.css 里 .md-content 那份的行距约定（全局是 1.7），
   不走 chrome 的 --lh-* 阶梯：这里是连续正文，而阶梯的比例（1.43）是给界面文字
   定的，用在成段的正文上偏挤。字号折到 14px 是为了让下面那几个 em 的子元素
   （h1/h2/h3、code）有一个干净的基数。 */
.md-content {
  font-size: 14px;
  line-height: 1.6;
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
  border-radius: 8px;
}
.md-content :deep(table) {
  display: block;
  width: max-content;
  max-width: 100%;
  overflow-x: auto;
}
/* 气泡里那一层往回走到「面」那一级配一条更浅的线：一层比一层亮，和两侧的气泡
   底色（--fill / --fill-2）都分得开。留在 --fill 的话它和左侧气泡同色，糊成一块。 */
.md-content :deep(code) {
  font-family: var(--font-mono);
  background: var(--surface);
  border: 1px solid var(--line);
  padding: 0.5px 5px;
  border-radius: var(--radius-sm);
  font-size: 0.88em;
}
.md-content :deep(pre) {
  background: var(--surface);
  border: 1px solid var(--line);
  padding: 11px 13px;
  border-radius: 8px;
  overflow-x: auto;
}
.md-content :deep(pre) code {
  background: none;
  padding: 0;
}
.md-content :deep(blockquote) {
  margin: 6px 0;
  padding-left: 12px;
  border-left: 2px solid var(--line-2);
  color: var(--muted);
}
</style>
