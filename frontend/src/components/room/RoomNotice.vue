<script setup lang="ts">
// 平台替这个房间写下的一条：出了什么事、谁在处理、详情在一次点击之后。
//
// 一条通知和一条消息长得不一样是有意的——它不是谁说的话。它按「是什么」分成两类：
// - **谁做了一件事**（芝士改了文件、检查没过、工作电脑就绪、平台记下一次报错）：
//   一种行，记号在头像列、事件装在左沿对齐消息正文的底色块里（`AgentNoticeFrame`）。
//   档位之间只差块里装什么；轻重只改块的底色和状态字的颜色，不改形状。
// - **房间里发生的事**（有人加入、话题归档）：没有署名的一行淡字，居中。
//
// 这里只画已经判好档的东西：判档在 lib/platformNotice.ts，署名和时间由房间算好
// 传进来。它不认识名册，也不认识 socket。
import type { Block } from '../../cx_types'
import type { FaceState } from '../../lib/agentFace'
import type { DocReviewRequest } from '../../lib/docReview'
import type { OpenedDocument } from '../../lib/docReview'
import type { NoticeAgent, PlatformNotice } from '../../lib/platformNotice'

import { computed, nextTick, ref, watch } from 'vue'

import { parseDiffLines } from '../../lib/diff'
import { docLineInTurn, docLineOf, isDocLine } from '../../lib/docEditLine'
import { repeatsLine } from '../../lib/noticeRepeats'
import { noticeNotified, noticeText } from '../../lib/noticeText'
import { confirmTarget, environmentTarget } from '../../lib/platformNotice'
import { renderPlain as renderPlainWith } from '../../lib/renderMessage'
import AgentNoticeFrame from '../AgentNoticeFrame.vue'
import NavLink from '../common/NavLink.vue'

import DocEditLine from './DocEditLine.vue'
import MailDraftCard from './MailDraftCard.vue'
import RollingNumber from './RollingNumber.vue'

import { t } from '@/i18n'

const props = defineProps<{
  /** 这条通知贴在哪个块上。 */
  block: Block
  notice: PlatformNotice
  /** 折起来的那一串同类事件（`agent-status` 档要拿它画现场）。 */
  run: Block[]
  /** 署名：平台替哪位队友写的这一条。算不出来就是 null，框里不画名字。 */
  agent: NoticeAgent | null
  /** 接在同一位队友上一条事件行下面：不再重复头像和名字。 */
  cont?: boolean
  /** 这一行的头像是这位队友最近出现的那个，它正在干活（或刚干完）：头像的表情。 */
  face?: FaceState | null
  /** 头像在动时，悬停看到的那一句。 */
  faceLabel?: string | null
  /** 头像在动时，读屏读到的那一句：只有状态，不带在走的秒数。 */
  faceStatus?: string | null
  time: string
  /** 这个房间当前那位 AI 队友的名字，`fold` 档在「…处理中」里用。 */
  agentName: string
  /** handle→昵称 / 话题 id→标题，正文里的 token 靠它渲染成可点的 chip。 */
  refs: { mentionNames: Record<string, string>; topicTitles: Record<string, string> }
  /** 房间允许在这一条上重试：它是最新的一条、房间没在跑、也没归档。可重试与否
   *  是提示自己说的（`retryable`），两者都成立才画按钮。 */
  canRetry?: boolean
  /** 重试请求已经发出、还没回来。 */
  retrying?: boolean
  /** 房间所在的项目：「去确认」要带人去项目级的页面。 */
  projectId?: string | null
  /** 合成一行的那一串里的一条：只画这一条自己的内容，外框（头像、名字）归合起来的那一行。 */
  inline?: boolean
  /** 合成一行的那一串，点开后每一条自己的时间。 */
  timeOf?: (iso: string) => string
}>()

const emit = defineEmits<{
  (e: 'open-resource', resource: string, turnId?: string, review?: DocReviewRequest, document?: OpenedDocument): void
  (e: 'open-card', taskId: string): void
  (e: 'retry'): void
  /** 「置顶了一条消息」那一行的「查看」：去被置顶的那一条。 */
  (e: 'jump', blockId: string): void
  // 「标题自动更新为…」那一行的撤销：带着这一行自己的 id，后端据此找回原标题。
}>()

// 队友改了一份文档那一行（lib/docEditLine）：在它自己那一轮里的，外框照旧是它的头像和
// 名字（接在这一轮上面的话下面时只剩空着的头像列）；不在哪一轮里的，是不署名的一行。
const MENTION = /<@([\w-]+)>/g
function nameOf(handle: string): string {
  if (props.agent?.handle === handle) return props.agent.name
  return props.refs.mentionNames[handle] || handle
}
const docLine = computed(() => docLineOf(props.block, props.notice, nameOf))
// 这一行的名字按 nameOf 的叫法画（这一轮的队友按它在这里的名字）。
const docNames = computed(() =>
  props.agent?.handle
    ? { ...props.refs, mentionNames: { ...props.refs.mentionNames, [props.agent.handle]: props.agent.name } }
    : props.refs
)
const docInTurn = computed(() => !!docLine.value && !!props.agent && docLineInTurn(props.block))

const name = computed(() => (docLine.value && !docInTurn.value ? null : props.agent?.name ?? null))
/** 没有署名的一行字：房间里发生的事，不是谁做的事。 */
const happening = computed(() => {
  const own = props.notice.mode === 'repeats' ? props.notice.rows[0].notice : props.notice
  return own?.mode === 'plain' && !props.agent
})

// 这件事有多重，外框的底色块按它上色：事故是 danger，等人处理的折叠行是 warn。
const tone = computed(() =>
  props.notice.mode === 'incident'
    ? 'danger'
    : props.notice.mode === 'fold' && props.notice.who === 'human'
      ? 'warn'
      : null
)

// 外框（头像、名字、时间）；合成一行里的一条没有自己的外框。
const frame = computed(() =>
  props.inline
    ? {}
    : {
        name: name.value,
        handle: props.agent?.handle ?? null,
        mark: docLine.value ? 'mdi-file-document-edit-outline' : undefined,
        cont: props.cont,
        face: props.face ?? null,
        faceLabel: props.faceLabel ?? null,
        faceStatus: props.faceStatus ?? null,
        time: props.time,
        tone: tone.value,
      }
)

// 同一个人连着做的几件事合成的那一行：收起时一句概括，点开列出每一条。
const repeatsOpen = ref(false)
const repeatsText = computed(() => {
  if (props.notice.mode !== 'repeats') return ''
  // 队友做的，名字照队友的叫法；人做的，正文里那个 <@handle> 渲染成他的名字。改文档的
  // 那几行合成的，和它们各自一样写成普通的名字，不画 @。
  const docs = props.notice.rows.every((row) => isDocLine(row.block))
  const actor = docs ? props.notice.actor.replace(MENTION, (_m, handle: string) => nameOf(handle)) : props.notice.actor
  return repeatsLine(props.notice.rows, props.agent?.name ?? actor)
})

/** 本轮改动先列三个文件；其余的点一下再展开，不必去别处看。 */
const FILES_SHOWN = 3
const allFiles = ref(false)
const changes = computed(() => (props.notice.mode === 'turn-summary' ? props.notice.changes : null))
const shownFiles = computed(
  () => (allFiles.value ? changes.value?.files : changes.value?.files.slice(0, FILES_SHOWN)) ?? []
)
/** 「另 N 个文件」里的 N：没列出来的，包括后端就没有发过来的那几个。 */
const hiddenFiles = computed(() => (changes.value ? changes.value.filesTotal - shownFiles.value.length : 0))

// 同类事件又来了一次：不加新行，这一行的计数滚一格、整行亮一下，说「又一次」。
const repeats = computed(() => (props.notice.mode === 'fold' ? props.notice.count : 0))
const bumped = ref(false)
watch(repeats, async (next, prev) => {
  if (next <= prev) return
  bumped.value = false
  await nextTick()
  requestAnimationFrame(() => (bumped.value = true))
})

const showRetry = computed(
  () =>
    !!props.canRetry &&
    ((props.notice.mode === 'incident' && props.notice.incident.retryable) ||
      (props.notice.mode === 'fold' && props.notice.retryable))
)

// 芝士整理的技能：这一行直接通到要确认的那一条。
const confirmAt = computed(() => confirmTarget(props.block, props.projectId))
const environmentAt = computed(() => environmentTarget(props.block, props.projectId))

// 提示里的人只是被提到（名字引用、不带 @）；这件事同时叫醒的那一位（交给它的 AI 队友、
// 被派去修合并后检查的那一位）才是带 @ 的实心 chip（lib/noticeText 的 noticeNotified）。
const refs = computed(() => ({ ...props.refs, notified: noticeNotified(props.block) }))

function renderPlain(text: string): string {
  return renderPlainWith(text, refs.value)
}

// 文档 diff 里的空行不画出来：一行只有空白时留一个空格子比留一条假的「改动」好。
function docDiffText(line: string): string {
  const text = line.slice(1)
  return /^(?:\s|&nbsp;)*$/.test(text) ? '' : text
}

// 「创建了任务」那一行（更早的叫「派出一条活」）带着那个任务，按钮打开它。
const splitTask = computed(() => {
  if (props.notice.mode !== 'action' || !['split', 'task_created'].includes(props.notice.resource)) return null
  const id = (props.block.meta as Record<string, unknown> | null | undefined)?.task_id
  return typeof id === 'string' && id ? id : null
})

// 「置顶了一条消息」那一行：被置顶的是哪一条，行尾一颗「查看」去它那里。
const pinnedBlock = computed(() => {
  const id = (props.block.meta as Record<string, unknown> | null | undefined)?.pinned_block_id
  return typeof id === 'string' && id ? id : null
})

// 哪些资源的行尾带一颗「去看看」按钮，以及那颗按钮上写什么（目录里的键）。
const ACTION_META: Record<string, { btn: string }> = {
  doc: { btn: 'work.room.notice.action.doc' },
  split: { btn: 'work.room.notice.action.split' },
  task_created: { btn: 'work.room.notice.action.split' },
  accept: { btn: 'work.room.notice.action.accept' },
  notify: { btn: '' },
}
</script>

<template>
  <!-- 房间里发生的事：居中一行淡字。Content may carry a <@handle> actor token
       (归档/加入…): render it through the SAME token→chip path as messages. -->
  <div v-if="happening && notice.mode === 'repeats'" class="room-happening im-event">
    <button
      type="button"
      class="room-happening__repeats"
      :aria-expanded="repeatsOpen"
      data-testid="notice-repeats"
      @click="repeatsOpen = !repeatsOpen"
    >
      <span v-html="renderPlain(repeatsText)" />
      <v-icon size="14" aria-hidden="true">{{ repeatsOpen ? 'mdi-chevron-down' : 'mdi-chevron-right' }}</v-icon></button
    ><span class="room-happening__time"> · {{ time }}</span>
    <template v-if="repeatsOpen">
      <RoomNotice
        v-for="row in notice.rows"
        :key="row.block.id"
        class="room-happening__member"
        :block="row.block"
        :notice="row.notice!"
        :run="row.run"
        :agent="null"
        :time="timeOf?.(row.block.created_at) ?? ''"
        :agent-name="agentName"
        :refs="refs"
        :project-id="projectId"
        inline
        @open-resource="(...a) => emit('open-resource', ...a)"
        @open-card="emit('open-card', $event)"
        @jump="emit('jump', $event)"
      />
    </template>
  </div>
  <div v-else-if="happening" class="room-happening im-event">
    <span v-html="renderPlain(noticeText(block))" /><span v-if="time" class="room-happening__time"> · {{ time }}</span>
    <template v-if="pinnedBlock">
      <span class="room-happening__time"> · </span>
      <button type="button" class="room-happening__go" @click="emit('jump', pinnedBlock)">
        {{ t('work.room.pin.view') }}
      </button>
    </template>
  </div>
  <component
    :is="inline ? 'div' : AgentNoticeFrame"
    v-else
    v-bind="frame"
    :class="{ 'notice-bump': bumped, 'notice-inline': inline }"
    @animationend="bumped = false"
  >
    <div
      v-if="notice.mode === 'incident'"
      class="sys-row sys-row--danger platform-incident"
      role="alert"
      :data-error-code="notice.incident.code"
      data-testid="platform-error-card"
    >
      <div class="sys-line">
        <span class="sys-text sys-lead">{{ notice.incident.title }}</span>
        <!-- 「可以重试」说的是一个动作，只在旁边真有一颗重试按钮时才画得出来。卡上
             不是此刻能重试的那一张时（后来的事盖过了它、房间还在跑、输入框关着），
             它一个字的落点都没有 —— 摆出来就是一句点不动的假按钮。其余几档
             （正在清理 / 正在恢复 / 需要管理员处理）说的是状态，照常画。 -->
        <span v-if="notice.incident.statusKind !== 'retryable'" class="sys-who">{{ notice.incident.status }}</span>
        <button v-if="showRetry" type="button" class="sys-btn" :disabled="retrying" @click="emit('retry')">
          {{ t('work.room.retry.action') }}
        </button>
      </div>
      <details v-if="notice.rest" class="sys-more">
        <summary class="sys-line">
          <span class="sys-text">{{ notice.lead }}</span>
          <v-icon class="sys-chev" size="14">mdi-chevron-right</v-icon>
        </summary>
        <pre class="sys-detail">{{ notice.rest }}</pre>
      </details>
      <div v-else class="sys-line">
        <span class="sys-text">{{ notice.lead }}</span>
      </div>
    </div>
    <!-- 本轮摘要 (spec §8.5 变更提醒): 这一轮改了哪些文件 + 顺带更新了什么。
     「查看改动」是这一行唯一的动作 —— 采纳是话题级的一次性动作，不是
     每轮都问一遍的东西（§14.6）。 -->
    <div v-else-if="notice.mode === 'turn-summary'" class="sys-row turn-summary">
      <div class="sys-line">
        <span class="sys-text">
          <template v-if="notice.changes">
            {{ t('work.room.notice.filesChanged', { n: notice.changes.filesTotal }) }}
            <span class="sys-num">+{{ notice.changes.added }} −{{ notice.changes.removed }}</span>
          </template>
          <template v-for="(act, ai) in notice.actions" :key="ai">
            <span v-if="ai > 0 || notice.changes" class="sys-sep"> · </span>
            <span v-html="renderPlain(act.text)" />
          </template>
          <button
            v-if="notice.changes"
            type="button"
            class="sys-btn sys-btn--inline"
            @click="emit('open-resource', 'changes', notice.turnId ?? undefined)"
          >
            {{ t('work.room.notice.viewChanges') }}
          </button>
        </span>
      </div>
      <!-- 竖排，每个文件带自己的增删：路径一列，+n 和 −n 紧挨着各自对齐。 -->
      <ul v-if="shownFiles.length" class="sys-files" :aria-label="t('work.room.notice.changedFiles')">
        <li v-for="file in shownFiles" :key="file.path">
          <span class="sys-files__path" :title="file.path">{{ file.path }}</span>
          <span class="sys-files__added">+{{ file.added }}</span>
          <span class="sys-files__removed">−{{ file.removed }}</span>
        </li>
      </ul>
      <button
        v-if="hiddenFiles > 0 && !allFiles && notice.changes!.files.length > FILES_SHOWN"
        type="button"
        class="sys-files-more"
        @click="allFiles = true"
      >
        {{ t('work.room.notice.moreFiles', { n: hiddenFiles }) }}
      </button>
      <div v-else-if="hiddenFiles > 0" class="sys-files-rest">
        {{ t('work.room.notice.moreFiles', { n: hiddenFiles }) }}
      </div>
    </div>
    <DocEditLine
      v-else-if="docLine"
      :block="block"
      :line="docLine"
      :in-turn="docInTurn"
      :names="docNames"
      @open-resource="(...a) => emit('open-resource', ...a)"
    />
    <!-- 芝士这轮改了平台上的什么东西（没能折进本轮摘要的那一条） -->
    <div v-else-if="notice.mode === 'action'" class="sys-row action-card">
      <div class="sys-line">
        <!-- notice.text may carry a <@handle> actor token (编辑了文档): render
         through the shared token→chip path so the actor is clickable. -->
        <span class="sys-text">
          <span v-html="renderPlain(notice.text)" />
          <button
            v-if="ACTION_META[notice.resource]?.btn"
            type="button"
            class="sys-btn sys-btn--inline"
            @click="
              splitTask
                ? emit('open-card', splitTask)
                : emit('open-resource', notice.resource, block.turn_id ?? undefined)
            "
          >
            {{ t(ACTION_META[notice.resource].btn) }}
          </button>
        </span>
      </div>
      <details v-if="notice.detail" class="sys-more">
        <summary class="sys-line">
          <span class="sys-text">{{ notice.detailLabel || t('work.room.notice.showDetail') }}</span>
          <v-icon class="sys-chev" size="14">mdi-chevron-right</v-icon>
        </summary>
        <div v-if="notice.resource === 'doc'" class="doc-edit-diff" :aria-label="t('work.room.notice.docDiff')">
          <template v-for="(line, index) in parseDiffLines(notice.detail)" :key="index">
            <div
              v-if="(line.kind === 'add' || line.kind === 'del') && docDiffText(line.text)"
              class="doc-edit-line"
              :class="`doc-edit-line--${line.kind}`"
              :aria-label="
                line.kind === 'add'
                  ? t('work.room.notice.added')
                  : line.kind === 'del'
                    ? t('work.room.notice.removed')
                    : undefined
              "
            >
              <span class="doc-edit-mark" aria-hidden="true">{{
                line.kind === 'add' ? '+' : line.kind === 'del' ? '−' : ' '
              }}</span>
              <span>{{ docDiffText(line.text) }}</span>
            </div>
          </template>
        </div>
        <pre v-else class="sys-detail">{{ notice.detail }}</pre>
      </details>
    </div>
    <!-- 折叠行: CI 没过 / 闸门红了 / 轮次失败… summary 一行就够决定「出了
     什么事、归谁管」，日志和原话在一次点击之后。连着来的同类事件折成一
     条带 ×N，但每一次的原话都还在展开区里，一条都没扔。 -->
    <details
      v-else-if="notice.mode === 'fold'"
      class="sys-row"
      :class="{ 'sys-row--warn': notice.who === 'human' }"
      data-testid="platform-notice"
    >
      <summary class="sys-line">
        <!-- 这一行里提到的人是 `<@handle>` token，和别的提示行一样画成名字，不露出原文。 -->
        <span class="sys-text" :class="{ 'sys-lead': notice.who === 'human' }" v-html="renderPlain(notice.line)" />
        <span v-if="notice.quote" class="sys-quote" data-user-content>{{
          t('work.room.notice.quote', { text: notice.quote })
        }}</span>
        <v-icon v-if="notice.occurrences.length" class="sys-chev" size="14">mdi-chevron-right</v-icon>
        <span v-if="notice.count > 1" class="sys-num">×<RollingNumber :value="notice.count" /></span>
        <span v-if="notice.whoLabel" class="sys-who">{{
          notice.who === 'cheese' ? t('work.room.notice.working', { name: name || agentName }) : notice.whoLabel
        }}</span>
        <NavLink v-if="confirmAt" :to="confirmAt" class="sys-btn" data-testid="notice-confirm" @click.stop>
          {{ t('work.room.notice.goConfirm') }}
        </NavLink>
        <NavLink v-if="environmentAt" :to="environmentAt" class="sys-btn" data-testid="notice-environment" @click.stop>
          {{ t('work.room.notice.goEnvironment') }}
        </NavLink>
        <button v-if="showRetry" type="button" class="sys-btn" :disabled="retrying" @click.prevent.stop="emit('retry')">
          {{ t('work.room.retry.action') }}
        </button>
      </summary>
      <div class="sys-fold">
        <div v-for="(occ, oi) in notice.occurrences" :key="oi" class="sys-occurrence">
          <div class="sys-meta">
            {{ occ.label || t('work.room.notice.detail')
            }}<template v-if="notice.count > 1"> · {{ occ.line }}</template>
          </div>
          <pre v-if="occ.detail" class="sys-detail">{{ occ.detail }}</pre>
        </div>
      </div>
    </details>
    <div v-else-if="notice.mode === 'mail-draft'" class="sys-row">
      <div class="sys-line">
        <span class="sys-text" v-html="renderPlain(noticeText(block))" />
      </div>
      <MailDraftCard :mail="notice.mail" :outcome="notice.outcome" :names="refs.mentionNames" />
    </div>
    <div v-else-if="notice.mode === 'plain'" class="sys-row im-event">
      <div class="sys-line">
        <span class="sys-text" v-html="renderPlain(noticeText(block))" />
      </div>
    </div>
    <div v-else-if="notice.mode === 'repeats'" class="sys-row">
      <button
        type="button"
        class="sys-line sys-repeats"
        :aria-expanded="repeatsOpen"
        data-testid="notice-repeats"
        @click="repeatsOpen = !repeatsOpen"
      >
        <span class="sys-text" v-html="renderPlain(repeatsText)" />
        <v-icon class="sys-chev" size="14">{{ repeatsOpen ? 'mdi-chevron-down' : 'mdi-chevron-right' }}</v-icon>
      </button>
      <div v-if="repeatsOpen" class="sys-repeats__list">
        <RoomNotice
          v-for="row in notice.rows"
          :key="row.block.id"
          :block="row.block"
          :notice="row.notice!"
          :run="row.run"
          :agent="agent"
          :time="''"
          :agent-name="agentName"
          :refs="refs"
          :project-id="projectId"
          inline
          @open-resource="(...a) => emit('open-resource', ...a)"
          @open-card="emit('open-card', $event)"
          @jump="emit('jump', $event)"
        />
      </div>
    </div>
  </component>
</template>

<style scoped>
/* ---------------------------------------------------------------------------
   事件行里装的东西。几何（记号列、底色块、时间）归外框 AgentNoticeFrame，
   这里只管那一行字：正文、数字、状态、按钮、展开区。
   --------------------------------------------------------------------------- */
details.sys-row > summary {
  cursor: pointer;
  list-style: none;
}
details.sys-row > summary::-webkit-details-marker,
.sys-more > summary::-webkit-details-marker {
  display: none;
}
.sys-more > summary {
  cursor: pointer;
  list-style: none;
}
.sys-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 8px;
  min-width: 0;
  min-height: 20px; /* 和 20px 的记号同高，单行时字落在记号中线上 */
}
.sys-text {
  flex: 0 1 auto;
  min-width: 0;
  overflow-wrap: anywhere;
}
/* 需要人读的那一句（出了什么事）比旁边的说明深一档。 */
.sys-lead {
  color: var(--text);
}
/* 一个人写的那句话（退回理由）：跟在这一行后面，用引号和正文色标出是他的原话。 */
.sys-quote {
  min-width: 0;
  color: var(--text);
  overflow-wrap: anywhere;
}
/* 状态和按钮推到行尾：扫一列就知道有没有在等自己。 */
.sys-who {
  flex: none;
  margin-left: auto;
  font-size: 12px;
  color: var(--faint);
}
.sys-text + .sys-btn,
.sys-chev + .sys-btn {
  margin-left: auto;
}
/* 「撤销」「查看文档」这类只属于这一句话的动作，接在这句话的末尾，跟着字一起折行。
   推到行尾的话，短句后面隔着大半行空白，长句一折行它就自己掉到下一行的最右边，
   两种情况都看不出它是哪句话的。 */
.sys-btn.sys-btn--inline {
  display: inline-flex;
  align-items: center;
  height: 22px;
  margin-left: 8px;
  vertical-align: middle;
  font-size: 12px;
}
.sys-num {
  flex: none;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--faint);
}
/* 可展开的都带同一个箭头，点开转 90°。 */
.sys-chev {
  flex: none;
  margin-left: -4px;
  color: var(--faint);
  transition: transform var(--dur-base) var(--ease-standard);
}
details[open] > summary > .sys-chev {
  transform: rotate(90deg);
}
/* 展开收起过渡高度，不跳（设计系统 §9.2）。不认 ::details-content 的浏览器照旧
   直接展开，什么都不缺。 */
.sys-row,
.sys-more {
  interpolate-size: allow-keywords;
}
details::details-content {
  height: 0;
  overflow: clip;
  transition:
    height var(--dur-quick) var(--ease-in),
    content-visibility var(--dur-quick) allow-discrete;
}
details[open]::details-content {
  height: auto;
  transition:
    height var(--dur-base) var(--ease-out),
    content-visibility var(--dur-base) allow-discrete;
}
/* 又来了一次：整行从 --fill 褪回去。 */
.notice-bump {
  animation: notice-bump var(--dur-slow) var(--ease-out);
}
@keyframes notice-bump {
  from {
    background-color: var(--fill-2);
  }
}
/* 这一列里要人动手的那一步，全是这一种中性小按钮。琥珀只留给发送和审阅。 */
.sys-btn {
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
.sys-btn:hover:not(:disabled) {
  background: var(--fill);
  border-color: var(--faint);
}
.sys-btn:disabled {
  cursor: default;
  opacity: 0.6;
}
.sys-meta {
  font-size: 12px;
  color: var(--faint);
  margin-top: 4px;
}
.doc-edit-diff {
  margin-top: 8px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  overflow: hidden;
  font-size: 13px;
  color: var(--text);
}
.doc-edit-line {
  display: flex;
  gap: 8px;
  padding: 4px 8px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.doc-edit-mark {
  flex: 0 0 1em;
}
.doc-edit-line--add {
  background: var(--ok-wash);
  color: var(--ok-ink);
}
.doc-edit-line--del {
  background: var(--danger-wash);
  color: var(--danger-ink);
}
.sys-detail {
  margin: 4px 0 6px;
  padding: 8px 10px;
  max-height: 240px;
  overflow: auto;
  border-radius: var(--radius-sm);
  background: var(--fill);
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: var(--lh-12);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  color: var(--text);
}
.sys-occurrence + .sys-occurrence {
  border-top: 1px solid var(--line);
  padding-top: 4px;
}
/* 本轮改动的文件：一列路径，+n 和 −n 紧挨着各自对齐（右对齐的 +n、左对齐的 −n），
   整块只有内容那么宽——拉满整行的话，数字离路径远到对不上是哪一行的。 */
.sys-files {
  display: grid;
  grid-template-columns: minmax(0, max-content) auto auto;
  justify-content: start;
  align-items: baseline;
  margin: 4px 0 2px;
  padding: 0;
  list-style: none;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}
.sys-files li {
  display: contents;
}
.sys-files__path {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sys-files__added {
  padding-left: 16px;
  text-align: right;
  color: var(--ok-ink);
}
.sys-files__removed {
  padding-left: 6px;
  color: var(--danger-ink);
}
.sys-files-more,
.sys-files-rest {
  padding: 0;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}
.sys-files-more {
  cursor: pointer;
}
.sys-files-more:hover {
  color: var(--ink);
  text-decoration: underline;
}
.sys-sep {
  color: var(--faint);
}
/* 轻重的底色归外框的底色块（`tone`）；这里只给状态字上同一档颜色。 */
.sys-row--warn .sys-who {
  color: var(--warn-ink);
}
.sys-row--danger .sys-who {
  color: var(--danger-ink);
}

/* 房间里发生的事：谁都没做这件事，所以它不进头像列，也不上正文轴，居中一行淡字。
   它也是时间线里的一行，和事件行（AgentNoticeFrame 的 .notice-row）、消息行
   （room-row.css 的 .im-row）一样：离屏的不渲染，但留在 DOM 里 —— Ctrl+F、读屏、
   选中都还找得到它。`auto 20px` 是这一行「12px 淡字」的估计高度，只在从未渲染过时
   占位；`auto` 让渲染过的行记住真实高度。测量帧（.cv-measure）里按真实高度铺开。 */
.room-happening {
  margin: 10px 16px;
  text-align: center;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--faint);
  overflow-wrap: anywhere;
  content-visibility: auto;
  contain-intrinsic-size: auto 20px;
}
/* 测量帧（向上翻页补偿、scrollIntoView）：这一窗里的行按真实高度铺开。见
   lib/contentVisibility。 */
.cv-measure .room-happening {
  content-visibility: visible;
}
/* 居中的淡行（置顶、归档、加入、改设置……）里的频道、文件：淡字、不垫底，悬停加下划线。
   提到的人照常是不给底的一颗（.name-ref），叫醒了谁的那一颗照常是实心 @chip——这一行和
   别处同一条规则。 */
.room-happening :deep(.mention:is(.file-ref, .topic-ref)) {
  color: var(--muted);
  background: none;
  cursor: pointer;
}
.room-happening :deep(.mention:is(.file-ref, .topic-ref):hover) {
  text-decoration: underline;
}
.room-happening__go {
  padding: 0;
  border: 0;
  background: none;
  font: inherit;
  color: var(--accent-ink);
  cursor: pointer;
}
.room-happening__go:hover {
  text-decoration: underline;
}
/* 连着的同一种操作合成的那一行：点一下在原处列出每一条。 */
.room-happening__repeats {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 0;
  border: 0;
  background: none;
  color: inherit;
  font: inherit;
  cursor: pointer;
}
.room-happening__repeats:hover {
  color: var(--muted);
}
.room-happening__member {
  margin: 2px 0;
}
.sys-repeats {
  width: 100%;
  padding: 0;
  border: 0;
  background: none;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.sys-repeats__list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-top: 4px;
  padding-left: 10px;
  border-left: 1px solid var(--line);
}
</style>
