<script setup lang="ts">
// 一条消息：谁说的、什么时候、说了什么，以及能对它做什么。
//
// 人和芝士共用同一种形态——平铺，靠左，只有名字的字重不同。这是 #1447 定的：
// 这一栏里最长的一半内容是芝士的产出（markdown、diff、几十行），气泡装不下，
// 而「一对一时两种长相就是两个角色，三个人一混就没规律」。
//
// 它不认识名册，也不认识时间线：显示名、头像、时间、被回复的那一条，都是房间
// 算好传进来的。它自己只回答「这一块该画成什么」。
import type { MentionPoolEntry } from '../../composables/useRoomMentionPicker'
import type { Block, ReplyPreview, TodoItem, Topic } from '../../cx_types'
import type { FaceState } from '../../lib/agentFace'
import type { RefNames } from '../../lib/refChip'

import { computed, ref } from 'vue'

import { useMessageFold } from '../../composables/useMessageFold'
import { byAssistant, isAgentHandle } from '../../lib/authorship'
import {
  artifactKind,
  artifactName,
  askOptions,
  isImageBlock,
  isVideoBlock,
  replySnippet,
} from '../../lib/blockDisplay'
import { fileIcon, hasPagePreview, previewCanShowInRoom, suffixOf } from '../../lib/fileKind'
import { toldByMessage } from '../../lib/refChip'
import AskQuickReplies from '../ask/AskQuickReplies.vue'
import AttachmentImage from '../AttachmentImage.vue'
import AttachmentVideo from '../AttachmentVideo.vue'
import CheeseAvatar from '../CheeseAvatar.vue'
import ExternalTag from '../common/ExternalTag.vue'
import MarkdownView from '../common/MarkdownView.vue'
import UserAvatar from '../common/UserAvatar.vue'

import ChecklistMessage from './ChecklistMessage.vue'
import FileBlockMenu from './FileBlockMenu.vue'
import MessageAppCard from './MessageAppCard.vue'
import MessageEditor from './MessageEditor.vue'
import MessageFileCard from './MessageFileCard.vue'
import MessageQuote from './MessageQuote.vue'
import RollingNumber from './RollingNumber.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const props = defineProps<{
  block: Block
  /** 这里已经不能回答提问了（任务关了、频道归档了）：选项不再给点。 */
  askClosed?: boolean
  /** 这一行上的那道题，我有一条答复还在路上：选项先不给点（`AskQuickReplies` 的 answering）。 */
  askAnswering?: boolean
  /** 被回复的那一条；不在当前页时是随页带来的一行预览；没有就是 null。 */
  parent: Block | ReplyPreview | null
  parentName: string | null
  /** 同一个人连着说的第一条——只有它带头像和名字。 */
  runStart: boolean
  /** 同一个人隔了一阵又开口：重新带上名字和时间，但只空一小档。 */
  regroup?: boolean
  /** 这条是我自己说的（名字加重）。 */
  mine: boolean
  topicId: string | null
  authorName: string
  /** 设了个人备注时悬停看到的原名与 @handle。 */
  authorTitle?: string | null
  /** 真头像的地址；取不到就画按 handle 哈希的色块。 */
  avatar: string | null
  isAgent: boolean
  /** 说话的人是这个项目的外部成员（团队以外、被邀请进来的）——名字旁挂「外部」。 */
  external?: boolean
  time: string
  /** handle→昵称 / 话题 id→标题，正文里的 token 靠它渲染成可点的 chip。 */
  refs: RefNames
  /** 一个 handle 在界面上叫什么（反应条悬停列谁点了）。房间按名册给，和消息署名
   *  同一个来源；没给时队友写「芝士」、人写 handle。 */
  nameOfHandle?: (handle: string) => string
  /** 自己的 handle，用来标出哪些表情是自己点的。 */
  viewer: string
  /** 悬停条此刻停在这一行上（指针可能在悬停条上，不在这一行上）。 */
  active?: boolean
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
  outgoing?: { error?: string; failed: boolean; slow?: boolean } | null
  /**
   * 正在改这条自己发过的消息：正文换成输入框，里面先放着原文。
   * `saving` 是保存请求还在路上。
   *
   * 这一行同时挂上 `im-row--editing`（见 room-row.css）：编辑时要抬掉这一行的
   * containment，否则 @ 候选菜单弹不出这一行，人只看得见贴着框沿的一条。
   */
  editing?: boolean
  saving?: boolean
  /** 这一行在频道主线上、我能在那里说话：文件的 ⋯ 里能置顶到频道。 */
  pinnable?: boolean
  /** 这一行置顶在频道上：名字旁边、文件旁边带一个小图钉。 */
  pinned?: boolean
  /** 编辑时的 @ 候选：和发消息的输入区是同一份名册、同一份话题表。 */
  mentionPool?: MentionPoolEntry[]
  /** 编辑框自己 @ 得到的话题，和发消息那个输入区同一份表。 */
  topicList?: Topic[]
  /**
   * 人发的长消息也收起。支线和任务里的对话给它：人在那里贴需求、报错、长文，一条就
   * 占满一栏。频道主线和私聊不给，那里只收队友的回复。
   */
  foldPlain?: boolean
}>()

// 他自己的芝士替他写的这条：署名照旧是他，名字旁边标出来（docs/agent-principles.md 一、四）。
const writtenByAssistant = computed(() => byAssistant(props.block))

// 在动的头像：读出来和悬停看到的是「名字 · 它此刻在干什么」，点下去去现场。
const personLabel = computed(() =>
  props.faceLabel
    ? t('work.agentAvatar.live', { name: props.authorName, status: props.faceLabel })
    : props.authorTitle || props.authorName
)
const personName = computed(() =>
  props.faceStatus ? t('work.agentAvatar.live', { name: props.authorName, status: props.faceStatus }) : props.authorName
)

// 反应条悬停时列的是谁点的：写房间给的名字，不写 handle。芝士的署名是管道
// （`cheese-<hex>`），摆到屏幕上读的人只当成乱码；房间没给名字、或者认不出这一位时
// 写「芝士」，和消息署名的兜底一样。人查不到名字时照旧显示 handle。
//
// 名字由房间按名册算好传进来，不能拿 `refs.mentionNames` 顶：那张表带着 all/here
// 两个保留键，一个恰好叫 here 的人会被写成「在线成员」。
function reactionAuthors(authors: string[]): string[] {
  return authors.map(
    (handle) => props.nameOfHandle?.(handle) || (isAgentHandle(handle) ? t('work.room.defaultAgentName') : handle)
  )
}

// 读屏停在这一条上时听到的：谁、几点（还没送出去的那条是送达状态），再是正文开头一段。
// 行只能被脚本聚焦（tabindex=-1）：时间线让当前那一条成为 Tab 停靠点（useTimelineKeys）。
const itemLabel = computed(() =>
  t('work.room.chat.messageLabel', {
    name: props.authorName,
    time: props.time,
    text: replySnippet(props.block, told.value, 140),
  })
)

const emit = defineEmits<{
  (e: 'open-file', path: string): void
  (e: 'open-card', taskId: string): void
  (e: 'react', block: Block, emoji: string): void
  (e: 'ask-reply', block: Block, text: string): void
  /** 自己在那道题上的回答（或跳过）要撤回。 */
  (e: 'ask-withdraw', block: Block): void
  /** 这道题不回答，跳过。 */
  (e: 'ask-skip', block: Block): void
  (e: 'download', block: Block): void
  /** 跳到被回复的那一条。 */
  (e: 'jump', blockId: string): void
  (e: 'retry'): void
  (e: 'edit'): void
  (e: 'save-edit', text: string): void
  (e: 'cancel-edit'): void
  /** 自己的清单改了一步：改过之后的整份。 */
  (e: 'checklist', block: Block, items: TodoItem[]): void
  /** 文件的 ⋯：存进资料库、置顶到频道、取消置顶。 */
  (e: 'keep', block: Block): void
  (e: 'pin', block: Block): void
  (e: 'unpin', block: Block): void
}>()

// 一份文件（附件或芝士摆出来的东西）：画成文件，旁边带 ⋯。
const fileBlock = computed(() => props.block.kind === 'attachment' || props.block.kind === 'artifact')
const canOpenFile = computed(() => props.block.kind === 'artifact' || previewCanShowInRoom(props.block.content))
/** 芝士摆出来的「正在跑的应用」：内容是一句说明，不是文件路径。 */
const APP_MIME = 'application/x-cheesex-app'
/** 画成富卡片的那几种：能翻页的文档和表格，都是真文件（应用、MCP 视图不是）。 */
const richFileCard = computed(
  () =>
    canOpenFile.value &&
    props.block.mime_type !== APP_MIME &&
    (hasPagePreview(props.block.content) || ['xlsx', 'xlsm'].includes(suffixOf(props.block.content)))
)

// 作者改过它：正文后面标一句「已编辑」。
const edited = computed(() => !!props.block.meta?.edited_at)
/** 从资料库里挑来附上的那一份本来就在资料库里，不再给「保存到资料库」。 */
function inLibrary(path: string): boolean {
  return path.startsWith('library/')
}

// 队友的步骤清单：照结构画，不照正文画。
const checklist = computed(() => {
  const value = props.block.meta?.checklist
  return value && typeof value === 'object' ? value : null
})

// ---- 长回复收起 ----
// 一条很长的队友回复收成一段高度，底下给一颗「展开 / 收起」；`foldPlain` 时人发的
// 长消息也一样收。判据、量高和状态都在 composables/useMessageFold：支线顶部那条初始
// 消息用的是同一份。还没落库的那条（正在写、正在送）不收——它的正文每帧都在长。
const foldEl = ref<HTMLElement | null>(null)
const canFold = computed(() => (props.isAgent || !!props.foldPlain) && !props.outgoing && !props.editing)
const {
  foldable,
  expanded,
  clamped,
  foldPx,
  toggle: toggleFold,
} = useMessageFold(
  foldEl,
  canFold,
  computed(() => [props.block.content, props.editing] as const)
)

// A message's `<@handle>` went through the mention path when it was sent or
// edited, so each one told its person (drawn as the @chip), unless a
// members-only channel could not reach them.
const told = computed(() => toldByMessage(props.refs))
</script>

<template>
  <div
    class="im-row"
    :class="{
      'im-row--cont': !runStart,
      'im-row--regroup': runStart && regroup,
      'im-row--self': mine,
      'im-row--pending': !!outgoing,
      'im-row--dim': !!outgoing && (outgoing.slow !== false || outgoing.failed),
      'im-row--active': active,
      'im-row--editing': editing,
    }"
    :data-mid="block.id"
    :data-actions="outgoing ? undefined : ''"
    role="article"
    :aria-label="itemLabel"
    tabindex="-1"
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
        <!-- 真人头像一律交给 UserAvatar：挑过头像就上图，没挑过或图取不到就退成按
             handle 哈希的彩色首字母 —— 退化只此一处，加载失败也由它记进
             utils/avatarFailures，这里不再自己画、也不再 emit('avatar-error')。
             底色的种子用 handle（换成昵称会让每个人的颜色都变），变的只有色块里的字。 -->
        <UserAvatar v-else :avatar="avatar ?? ''" :name="authorName" :seed="block.author" :size="28" />
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
          <button type="button" class="im-name im-person" :data-handle="block.author" :title="authorTitle || undefined">
            {{ authorName }}
          </button>
          <ExternalTag v-if="external && !isAgent" />
          <span
            v-if="writtenByAssistant"
            class="chip-neutral im-on-behalf"
            data-testid="on-behalf-tag"
            :title="t('work.onBehalf.tagHint', { name: authorName })"
            >{{ t('work.onBehalf.tag') }}</span
          >
          <span class="im-time">{{ time }}</span>
          <v-icon v-if="pinned && !fileBlock" size="13" class="im-pin-mark" :aria-label="t('work.room.pin.pinned')"
            >mdi-pin</v-icon
          >
        </template>
      </div>
      <!-- B3: a reply shows the message it threads under -->
      <button v-if="parent" type="button" class="im-replied" @click="emit('jump', parent.id)">
        <v-icon size="12">mdi-reply</v-icon>
        {{ t('work.room.composer.replyTo', { name: parentName, text: replySnippet(parent, refs) }) }}
      </button>
      <!-- 一份文件：发来的附件（图片直接画成图），或芝士摆出来的东西。旁边一颗 ⋯ 收着
           这一份能做的事；置顶了的带一个小图钉。 -->
      <div v-if="fileBlock" class="im-file">
        <!-- 图片输入: an attachment block renders as the image itself
           (click opens it larger, in place, see ImageLightbox). 字节在
           AttachmentImage 里取——raw 端点只认 Authorization 头，裸挂 URL 是匿名请求。 -->
        <AttachmentImage v-if="isImageBlock(block)" :topic-id="topicId" :path="block.content" />
        <AttachmentVideo
          v-else-if="isVideoBlock(block)"
          can-open
          :topic-id="topicId"
          :path="block.content"
          @open="emit('open-file', block.content)"
          @download="emit('download', block)"
        />
        <!-- 芝士摆出来给人看的一份东西（`cheese show`），或对话里发来的可预览文档附件：
           点它交给拿着面板的那一层去开，走的是 <&path> 芯片同一条线。 -->
        <template v-else-if="canOpenFile">
          <!-- 能翻页的文档（PDF、Word、幻灯片）和表格画成富卡片（封面、版本、改过没有，
               底下自带打开/展开/下载）；芝士摆出来的应用画成带运行状态的卡片；其余（网页、
               markdown、代码）照旧一颗小卡片。 -->
          <MessageFileCard
            v-if="richFileCard"
            :topic-id="topicId"
            :path="block.content"
            :name="artifactName(block)"
            :kind="artifactKind(block)"
            :posted-at="block.created_at"
            downloadable
            @open="emit('open-file', block.content)"
            @download="emit('download', block)"
          />
          <MessageAppCard
            v-else-if="block.mime_type === APP_MIME"
            :conversation-id="block.conversation_id || topicId"
            :block-id="block.id"
            :description="block.content"
            @open="emit('open-file', block.content)"
          />
          <button
            v-else
            type="button"
            class="im-artifact"
            :title="t('work.room.message.openFile', { name: artifactName(block) })"
            @click="emit('open-file', block.content)"
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
          <BaseButton
            v-if="block.kind === 'attachment' && !richFileCard"
            kind="ghost"
            size="sm"
            icon="mdi-download-outline"
            :title="t('work.room.message.downloadFile', { name: artifactName(block) })"
            :aria-label="t('work.room.message.downloadFile', { name: artifactName(block) })"
            @click="emit('download', block)"
          />
        </template>
        <BaseButton
          v-else-if="block.kind === 'attachment'"
          kind="ghost"
          prepend-icon="mdi-file-document-outline"
          append-icon="mdi-download-outline"
          class="im-file-link"
          :title="t('work.room.message.downloadFile', { name: artifactName(block) })"
          @click="emit('download', block)"
        >
          <span class="text-truncate">{{ artifactName(block) }}</span>
        </BaseButton>
        <v-icon v-if="pinned" size="14" class="im-pin-mark" :aria-label="t('work.room.pin.pinned')">mdi-pin</v-icon>
        <FileBlockMenu
          :name="artifactName(block)"
          :can-open="canOpenFile"
          :can-download="block.kind === 'attachment' || richFileCard"
          :can-keep="
            (block.kind === 'artifact' || (block.kind === 'attachment' && !inLibrary(block.content))) && !!topicId
          "
          :pinnable="pinnable"
          :pinned="pinned"
          @open="emit('open-file', block.content)"
          @download="emit('download', block)"
          @keep="emit('keep', block)"
          @pin="emit('pin', block)"
          @unpin="emit('unpin', block)"
        />
      </div>
      <MessageEditor
        v-else-if="editing"
        :text="block.content"
        :saving="!!saving"
        :refs="refs"
        :mention-pool="mentionPool"
        :topic-list="topicList"
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
      <!-- 芝士问的一道题：题目本身就是卡片的标题，和选项、末行的输入框收在同一张卡里，
           不再是上一条消息的最后一段。卡片页头写谁问的；这一条是一串的第一条时，名字
           那一行就在卡片正上方，页头不再说一遍名字（「问你」而不是「芝士 问你」）。 -->
      <AskQuickReplies
        v-else-if="askOptions(block)"
        :block="block"
        :asker="runStart ? undefined : authorName"
        :names="refs.mentionNames"
        :closed="askClosed"
        :answering="askAnswering"
        :viewer="viewer"
        @reply="emit('ask-reply', block, $event)"
        @withdraw="emit('ask-withdraw', block)"
        @skip="emit('ask-skip', block)"
      >
        <MarkdownView class="im-text md-content" :source="block.content" as="chat" :names="told" />
      </AskQuickReplies>
      <template v-else>
        <div
          ref="foldEl"
          class="fold"
          :class="{ 'fold-clamped': clamped }"
          :style="clamped ? { maxHeight: `${foldPx}px` } : undefined"
        >
          <!-- 人说的话和芝士的一样按 Markdown 画：输入框里写成代码的，发出去也是代码。
               单个回车照样是换行（as="chat"）。还没落库的那条（正在写、正在送）不带
               「复制」：它的代码块还在长。 -->
          <MarkdownView
            class="im-text md-content"
            :source="block.content"
            as="chat"
            :names="told"
            :copy-code="!outgoing"
          />
          <!-- Fade at the cut edge, so a folded reply reads as "there is more" rather than "it ends here". -->
          <div v-if="clamped" class="fold-fade" aria-hidden="true" />
        </div>
        <button
          v-if="canFold && foldable"
          type="button"
          class="fold-toggle"
          :aria-expanded="expanded"
          @click="toggleFold"
        >
          <v-icon size="16">{{ expanded ? 'mdi-chevron-up' : 'mdi-chevron-down' }}</v-icon>
          {{ expanded ? t('work.room.message.showLess') : t('work.room.message.showMore') }}
        </button>
        <div v-if="edited" class="im-edited">{{ t('work.room.message.edited') }}</div>
      </template>
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
      <!-- Emoji reaction chips (Slack): count per emoji, own reactions
         highlighted; click toggles. 芝士's 👀 receipt lands here too. -->
      <TransitionGroup v-if="block.reactions?.length" tag="div" name="rx" class="rx-row">
        <button
          v-for="r in block.reactions"
          :key="r.emoji"
          type="button"
          class="rx-chip"
          :class="{ 'rx-chip--mine': r.authors.includes(viewer) }"
          :title="reactionAuthors(r.authors).join(t('work.room.roster.listSeparator'))"
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
<style scoped src="@/styles/message-fold.css"></style>

<style scoped>
/* 键盘走到一条消息时，焦点环画在行内（offset 取负）。行是整宽的，而且滚动区在
   水平方向会裁掉溢出的部分：正 offset 的环在手机上会被左右两边切掉，看不出光标
   停在哪一条。见 docs/design-system.md 的焦点圈一节。 */
.im-row:focus-visible {
  outline-offset: -2px;
}
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
/* 发件箱: 慢请求、断网或正在输入预览时淡一档，正常快发不闪灰。 */
.im-row--dim .im-text,
.im-row--dim .im-name {
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
/* 「已编辑」：元信息那一档的字，跟在正文后面。 */
.im-edited {
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--faint);
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
  /* 文件类型（「网页」「文档」）是读者要看的字，不是装饰：它挂在 .t-meta 上，
     那档的 --faint 浅色下只有 4.33:1，够不到 4.5:1（docs/design-system.md §2.6）。 */
  color: var(--muted);
  text-align: left;
}
.im-artifact__go {
  flex: none;
  color: var(--faint);
}
.im-file {
  display: flex;
  align-items: center;
  gap: 4px;
  max-width: 100%;
  min-width: 0;
}
.im-pin-mark {
  flex: none;
  color: var(--accent-ink);
}
.im-on-behalf {
  flex: none;
}
.im-file-link {
  max-width: 100%;
}
.im-file-link :deep(.v-btn__content) {
  min-width: 0;
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
   就是一段松一段紧。字号折到 15px，下面几个 em 的子元素（h1/h2/h3、code）有一个
   干净的基数。 */
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
/* 标题：一档字号、一档重量，从 h1 到 h6 依次收。以前三档都压成 1.02em，h4/h5/h6
   干脆没写，浏览器把它们排得比正文还小——一屏里分不出层级。顶到整段开头的标题不再
   多留一段上边距。 */
.md-content :deep(h1),
.md-content :deep(h2),
.md-content :deep(h3),
.md-content :deep(h4),
.md-content :deep(h5),
.md-content :deep(h6) {
  margin: 14px 0 6px;
  font-weight: 600;
  line-height: 1.35;
  color: var(--ink);
}
.md-content :deep(:is(h1, h2, h3, h4, h5, h6):first-child) {
  margin-top: 0;
}
.md-content :deep(h1) {
  font-size: 1.4em;
}
.md-content :deep(h2) {
  font-size: 1.22em;
}
.md-content :deep(h3) {
  font-size: 1.08em;
}
.md-content :deep(h4) {
  font-size: 1em;
}
.md-content :deep(h5) {
  font-size: 0.94em;
}
.md-content :deep(h6) {
  font-size: 0.88em;
  color: var(--muted);
}
.md-content :deep(ul),
.md-content :deep(ol) {
  margin: 4px 0;
  padding-left: 20px;
}
/* 嵌套的一层缩进一份，段间距比外层小：层次比外层清楚，但不撑出一段空白。 */
.md-content :deep(li > ul),
.md-content :deep(li > ol) {
  margin: 2px 0;
}
.md-content :deep(li) {
  margin: 2px 0;
}
.md-content :deep(li > p) {
  margin: 0 0 4px;
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
/* 代码块和它右上角的两颗：自动换行、复制。样式住在 `style.css` 的 `.md-content`
   一段里——预览面板用的是同一个渲染器，只写在这一处的话那边就画不出样子。 */
.md-content :deep(pre) {
  background: var(--surface);
  border: 1px solid var(--line);
  padding: 10px 12px;
  border-radius: var(--radius-md);
  max-width: 100%;
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
  padding: 2px 0 2px 12px;
  border-left: 2px solid var(--line-2);
  color: var(--muted);
}
.md-content :deep(blockquote > :first-child) {
  margin-top: 0;
}
.md-content :deep(blockquote > :last-child) {
  margin-bottom: 0;
}

/* 长回复收起的样式和频道里别处、支线页头共用的那一份在 styles/message-fold.css
   （收起来的那层、下切边的渐隐、居中那颗展开/收起）。 */
</style>
