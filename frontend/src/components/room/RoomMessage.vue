<script setup lang="ts">
// 一条消息：谁说的、什么时候、说了什么，以及能对它做什么。
//
// 人和芝士共用同一种形态——平铺，靠左，只有名字的字重不同。这是 #1447 定的：
// 这一栏里最长的一半内容是芝士的产出（markdown、diff、几十行），气泡装不下，
// 而「一对一时两种长相就是两个角色，三个人一混就没规律」。
//
// 它不认识名册，也不认识时间线：显示名、头像、时间、被回复的那一条，都是房间
// 算好传进来的。它自己只回答「这一块该画成什么」。
import type { Block, TodoItem } from '../../cx_types'
import type { FaceState } from '../../lib/agentFace'
import type { AskGroupAction, AskGroupState } from '../../lib/askGroupState'
import type { AskAction, AskFormState } from '../../lib/askPresentation'

import { computed } from 'vue'

import { artifactKind, artifactName, askOptions, isImageBlock, replySnippet } from '../../lib/blockDisplay'
import { fileIcon } from '../../lib/fileKind'
import { renderMarkdown as renderMarkdownWith, renderPlain as renderPlainWith } from '../../lib/renderMessage'
import { avatarColor, avatarInitial } from '../../utils/avatar'
import AskGroupFlow from '../ask/AskGroupFlow.vue'
import AskQuestionForm from '../ask/AskQuestionForm.vue'
import AttachmentImage from '../AttachmentImage.vue'
import CheeseAvatar from '../CheeseAvatar.vue'
import ExternalTag from '../common/ExternalTag.vue'

import ChecklistMessage from './ChecklistMessage.vue'
import MessageEditor from './MessageEditor.vue'
import MessageQuote from './MessageQuote.vue'
import RollingNumber from './RollingNumber.vue'

import { t } from '@/i18n'

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
  /** 悬停条此刻停在这一行上（指针可能在悬停条上，不在这一行上）。 */
  active?: boolean
  askState?: AskFormState
  askGroupState?: AskGroupState
  askGroupAnchor?: string
  askGroupFocus?: string
  /** 这条是队友此刻正在推进的清单（房间在跑，且是它最新的一条）。 */
  live?: boolean
  /** 这一条的头像是这位队友最近出现的那个，它正在干活（或刚干完）：头像的表情。 */
  face?: FaceState | null
  /** 头像在动时，悬停看到的那一句（现场顶上那一行）。 */
  faceLabel?: string | null
  /** 头像在动时，读屏读到的那一句：只有状态，不带在走的秒数，免得每秒再念一遍。 */
  faceStatus?: string | null
  /**
   * 这一条还没落库——已经在屏幕上，正在（或没能）送出去。淡一档，形状不变：
   * 它就是那条消息，不是另一种东西。`time` 那一格这时装的是送达状态。
   * 没送出去的那条带「重试」和「编辑」：编辑把原文放回输入框。
   */
  outgoing?: { error?: string; failed: boolean } | null
  /**
   * 正在改这条自己发过的消息：正文换成输入框，里面先放着 `editText`。
   * `saving` 是保存请求还在路上。
   */
  editing?: boolean
  editText?: string
  saving?: boolean
}>()

// 在动的头像：读出来和悬停看到的是「名字 · 它此刻在干什么」，点下去去现场。
const personLabel = computed(() =>
  props.faceLabel ? t('work.agentAvatar.live', { name: props.authorName, status: props.faceLabel }) : props.authorName
)
const personName = computed(() =>
  props.faceStatus ? t('work.agentAvatar.live', { name: props.authorName, status: props.faceStatus }) : props.authorName
)

const emit = defineEmits<{
  (e: 'open-file', path: string, taskId: string | null): void
  (e: 'open-topic', id: string): void
  (e: 'open-card', taskId: string): void
  (e: 'react', block: Block, emoji: string): void
  (e: 'ask-action', block: Block, action: AskAction): void
  (e: 'ask-group-action', block: Block, action: AskGroupAction): void
  (e: 'ask-group-focus', block: Block): void
  (e: 'download', block: Block): void
  /** 跳到被回复的那一条。 */
  (e: 'jump', blockId: string): void
  (e: 'avatar-error', handle: string): void
  (e: 'retry'): void
  (e: 'edit'): void
  (e: 'save-edit', text: string): void
  (e: 'cancel-edit'): void
  /** 自己的清单改了一步：改过之后的整份。 */
  (e: 'checklist', block: Block, items: TodoItem[]): void
}>()

// 作者改过它：正文后面标一句「已编辑」。
const edited = computed(() => !!props.block.meta?.edited_at)

// 队友的步骤清单：照结构画，不照正文画。
const checklist = computed(() => {
  const value = props.block.meta?.checklist
  return value && typeof value === 'object' ? value : null
})

function renderMarkdown(text: string): string {
  return renderMarkdownWith(text, props.refs)
}

function renderPlain(text: string): string {
  return renderPlainWith(text, props.refs)
}

// 芝士的回复里每个代码块右上角一颗「复制」。按钮是渲染之后加上去的，文字取自
// 这一处自己的文案，不来自消息内容，所以不必再过一遍净化。还没落库的那条（正在写、
// 正在送）不带：它的代码块还在长。
const agentHtml = computed(() =>
  props.outgoing
    ? renderMarkdown(props.block.content)
    : renderMarkdown(props.block.content)
        .replaceAll(
          '<pre>',
          `<div class="md-pre"><button type="button" class="md-copy">${t('work.room.message.copy')}</button><pre>`
        )
        .replaceAll('</pre>', '</pre></div>')
)

const COPIED_MS = 1500
async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}

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
      'im-row--active': active,
    }"
    :data-mid="block.id"
    :data-actions="outgoing ? undefined : ''"
  >
    <!-- avatar gutter: only on the first of a run -->
    <div class="im-gutter">
      <!-- 头像和名字点下去和正文里的 @chip 一样：去这个人的成员页。点击由房间委派
           （useChatPanel 的 onMessagesClick 认 data-handle），去处只有一处定义。 -->
      <!-- 队友在干活时，它在动的这个头像点下去是去「现场」看它在干什么（data-site），
           名字照旧去成员页。 -->
      <button
        v-if="runStart"
        type="button"
        class="im-person"
        :data-handle="block.author"
        :data-site="faceLabel ? '' : undefined"
        :aria-label="personName"
        :title="faceLabel ? undefined : personLabel"
      >
        <!-- 在动的头像那一句带着秒数，每秒换一次字。原生 title 一换字就收起再弹，
             悬停时每秒闪一下；这个气泡原地换字。 -->
        <v-tooltip v-if="faceLabel" activator="parent" location="top" :text="personLabel" />
        <CheeseAvatar v-if="isAgent" :size="28" :name="authorName" :handle="block.author" :state="face ?? null" />
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
      </button>
      <!-- 续话没有名字那一行，时间在悬停时出现在头像列里，和正文第一行对齐。 -->
      <span v-else-if="!outgoing" class="im-gutter-time">{{ time }}</span>
    </div>
    <!-- 还没送出去的续话同样没有名字那一行，送达状态（发送中 / 等待连接）就挂在
       行尾，一直在，不等悬停：断网时人要知道这几句为什么是淡的。 -->
    <span v-if="outgoing && !runStart && time" class="im-pending-state">{{ time }}</span>

    <div class="im-main">
      <div class="im-meta">
        <template v-if="runStart">
          <button type="button" class="im-name im-person" :data-handle="block.author">{{ authorName }}</button>
          <ExternalTag v-if="external && !isAgent" />
          <span class="im-time">{{ time }}</span>
        </template>
      </div>
      <!-- B3: a reply shows the message it threads under -->
      <button v-if="parent" type="button" class="im-replied" @click="emit('jump', parent.id)">
        <v-icon size="12">mdi-reply</v-icon>
        {{ t('work.room.composer.replyTo', { name: parentName, text: replySnippet(parent, refs) }) }}
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
        :title="t('work.room.message.downloadFile', { name: artifactName(block) })"
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
        :title="t('work.room.message.openFile', { name: artifactName(block) })"
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
      <MessageEditor
        v-else-if="editing"
        :text="editText ?? block.content"
        :saving="!!saving"
        @save="emit('save-edit', $event)"
        @cancel="emit('cancel-edit')"
      />
      <ChecklistMessage
        v-else-if="checklist"
        :checklist="checklist"
        :updated-at="block.meta?.edited_at ?? block.created_at"
        :edited="edited"
        :live="!!live"
        :editable="mine && !outgoing"
        @change="emit('checklist', block, $event)"
      />
      <template v-else-if="isAgent">
        <div class="im-text md-content" @click="onAgentTextClick" v-html="agentHtml" />
        <div v-if="edited" class="im-edited">{{ t('work.room.message.edited') }}</div>
      </template>
      <!-- 现场尊重原文: human text renders verbatim — newlines and
         spacing preserved (pre-wrap), no markdown reflow. 「已编辑」接在最后
         一个字后面，不另起一行。 -->
      <div v-else class="im-text im-text--verbatim">
        <span v-html="renderPlain(block.content)" /><span v-if="edited" class="im-edited">{{
          t('work.room.message.edited')
        }}</span>
      </div>
      <MessageQuote v-if="block.meta?.quoted_context" :quote="block.meta.quoted_context" />
      <div v-if="outgoing?.failed" class="outbox-fail" role="alert">
        <span class="outbox-fail__text">{{
          outgoing.error ? t('work.room.outbox.failed', { reason: outgoing.error }) : t('work.room.outbox.undelivered')
        }}</span>
        <button type="button" class="outbox-btn" @click="emit('retry')">{{ t('work.room.retry.action') }}</button>
        <button type="button" class="outbox-btn" :title="t('work.room.outbox.editHint')" @click="emit('edit')">
          {{ t('work.room.outbox.edit') }}
        </button>
      </div>
      <AskQuestionForm
        v-if="askOptions(block) && !block.meta?.ask_group"
        :block="block"
        :viewer="viewer"
        :names="refs.mentionNames"
        :state="askState"
        @action="emit('ask-action', block, $event)"
      />
      <template v-if="block.meta?.ask_group">
        <AskGroupFlow
          v-if="askGroupState && askGroupAnchor === block.id"
          :state="askGroupState"
          :viewer="viewer"
          :names="refs.mentionNames"
          :focus-block="askGroupFocus ?? block.id"
          :auto-focus="!!askGroupFocus"
          @action="emit('ask-group-action', block, $event)"
        />
        <button v-else-if="askGroupState" type="button" @click="emit('ask-group-focus', block)">
          {{ t('ask.group.open') }}
        </button>
      </template>
      <!-- 活引用 (eval A1): 升级出去的块指向它变成的那个地点。房间里
         升级出来的是一条支线，私聊里升级出来的才是房间——两个字段各指
         一张表，同时只会有一个非空。 -->
      <button
        v-if="block.upgraded_to_task_id || block.upgraded_to_topic_id"
        type="button"
        class="im-upgraded"
        @click="
          block.upgraded_to_task_id
            ? emit('open-card', block.upgraded_to_task_id)
            : emit('open-topic', block.upgraded_to_topic_id!)
        "
      >
        <v-icon size="13">mdi-arrow-top-right</v-icon>
        {{ block.upgraded_to_task_id ? t('work.room.message.upgradedToTask') : t('work.room.message.upgradedToTopic') }}
      </button>
      <!-- Emoji reaction chips (Slack): count per emoji, own reactions
         highlighted; click toggles. 芝士's 👀 receipt lands here too. -->
      <TransitionGroup v-if="block.reactions?.length" tag="div" name="rx" class="rx-row">
        <button
          v-for="r in block.reactions"
          :key="r.emoji"
          type="button"
          class="rx-chip"
          :class="{ 'rx-chip--mine': r.authors.includes(viewer) }"
          :title="r.authors.join(t('work.room.roster.listSeparator'))"
          @click="emit('react', block, r.emoji)"
        >
          <span class="rx-emoji">{{ r.emoji }}</span>
          <RollingNumber class="rx-count" :value="r.count" />
        </button>
      </TransitionGroup>
    </div>
  </div>
</template>

<style scoped src="./room-row.css"></style>

<style scoped>
/* B3: the "回复 X：…" cue above a reply. */
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
/* 「已编辑」：元信息那一档的字，跟在正文后面。 */
.im-edited {
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--faint);
}
.im-text--verbatim .im-edited {
  margin-left: 6px;
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
/* 引用 chip（@人 / 文件 / 话题）的样式在 style.css 里，一份定义给所有渲染这份
   markup 的地方用——动作卡和系统事件行里的同款 chip 不在 .im-text 里面，写在组件
   的 scoped 块里就只有对话栏看得见；现场那一栏也渲染同一份 chip。 */

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
  font-size: 15px;
  line-height: var(--lh-15-reading);
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
/* 任务清单（`- [ ]` / `- [x]`，队友的步骤清单就是这样写的）：勾选框顶替圆点，
   用中性色——浏览器默认的勾是系统蓝。 */
.md-content :deep(li:has(> input[type='checkbox'])) {
  list-style: none;
}
.md-content :deep(ul:has(> li > input[type='checkbox'])) {
  padding-left: 2px;
}
.md-content :deep(li > input[type='checkbox']) {
  margin: 0 6px 0 0;
  vertical-align: -2px;
  accent-color: var(--muted);
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
