<script setup lang="ts">
// 平台替这个房间写下的一条：出了什么事、谁在处理、详情在一次点击之后。
//
// 一条通知和一条消息长得不一样是有意的——它不是谁说的话。它按「是什么」分成两类：
// - **谁做了一件事**（芝士改了文件、检查没过、运行环境就绪、平台记下一次报错）：
//   一种行，记号在头像列、正文在消息的正文轴上、时间在行尾（`AgentNoticeFrame`）。
//   档位之间只差那一行里装什么；轻重只改底色和状态字的颜色，不改形状。
// - **房间里发生的事**（有人加入、话题归档）：没有署名的一行淡字，居中。
//
// 这里只画已经判好档的东西：判档在 lib/platformNotice.ts，署名和时间由房间算好
// 传进来。它不认识名册，也不认识 socket。
import type { Block } from '../../cx_types'
import type { PlatformNotice } from '../../lib/platformNotice'

import { computed, nextTick, ref, watch } from 'vue'

import { parseDiffLines } from '../../lib/diff'
import { renderPlain as renderPlainWith } from '../../lib/renderMessage'
import AgentNoticeFrame from '../AgentNoticeFrame.vue'
import CloudStartupStatus from '../CloudStartupStatus.vue'

import RollingNumber from './RollingNumber.vue'

import { t } from '@/i18n'

const props = defineProps<{
  /** 这条通知贴在哪个块上。 */
  block: Block
  notice: PlatformNotice
  /** 折起来的那一串同类事件（`agent-status` 档要拿它画现场）。 */
  run: Block[]
  /** 署名：平台替谁写的这一条。算不出来就是 null，框里不画名字。 */
  name: string | null
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
}>()

const emit = defineEmits<{
  (e: 'open-resource', resource: string, turnId?: string): void
  (e: 'retry'): void
}>()

/** 没有署名的一行字：房间里发生的事，不是谁做的事。 */
const happening = computed(() => props.notice.mode === 'plain' && !props.name)

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
const repeats = computed(() =>
  props.notice.mode === 'fold'
    ? props.notice.count
    : props.notice.mode === 'backend-error'
      ? props.notice.error.count ?? 0
      : 0
)
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

function renderPlain(text: string): string {
  return renderPlainWith(text, props.refs)
}

// 文档 diff 里的空行不画出来：一行只有空白时留一个空格子比留一条假的「改动」好。
function docDiffText(line: string): string {
  const text = line.slice(1)
  return /^(?:\s|&nbsp;)*$/.test(text) ? '' : text
}

// 哪些资源的行尾带一颗「去看看」按钮，以及那颗按钮上写什么。
const ACTION_META: Record<string, { btn: string }> = {
  doc: { btn: '查看文档' },
  decision: { btn: '查看决策记录' },
  topics: { btn: '' },
  milestone: { btn: '查看日历' },
  accept: { btn: '审阅' },
  notify: { btn: '' },
}
</script>

<template>
  <!-- 房间里发生的事：居中一行淡字。Content may carry a <@handle> actor token
       (归档/加入…): render it through the SAME token→chip path as messages. -->
  <div v-if="happening" class="room-happening im-event">
    <span v-html="renderPlain(block.content)" /><span class="room-happening__time"> · {{ time }}</span>
  </div>
  <AgentNoticeFrame v-else :name="name" :time="time" :class="{ 'notice-bump': bumped }" @animationend="bumped = false">
    <div
      v-if="notice.mode === 'incident'"
      class="sys-row sys-row--danger platform-incident"
      role="alert"
      :data-error-code="notice.incident.code"
      data-testid="platform-error-card"
    >
      <div class="sys-line">
        <span class="sys-text sys-lead">{{ notice.incident.title }}</span>
        <span class="sys-who">{{ notice.incident.status }}</span>
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
            改动了 {{ notice.changes.filesTotal }} 个文件
            <span class="sys-num">+{{ notice.changes.added }} −{{ notice.changes.removed }}</span>
          </template>
          <template v-for="(act, ai) in notice.actions" :key="ai">
            <span v-if="ai > 0 || notice.changes" class="sys-sep"> · </span>
            <span v-html="renderPlain(act.text)" />
          </template>
        </span>
        <button
          v-if="notice.changes"
          type="button"
          class="sys-btn"
          @click="emit('open-resource', 'changes', notice.turnId ?? undefined)"
        >
          查看改动
        </button>
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
    <!-- 芝士这轮改了平台上的什么东西（没能折进本轮摘要的那一条） -->
    <div v-else-if="notice.mode === 'action'" class="sys-row action-card">
      <div class="sys-line">
        <!-- notice.text may carry a <@handle> actor token (编辑了文档): render
         through the shared token→chip path so the actor is clickable. -->
        <span class="sys-text" v-html="renderPlain(notice.text)" />
        <button
          v-if="ACTION_META[notice.resource]?.btn"
          type="button"
          class="sys-btn"
          @click="emit('open-resource', notice.resource, block.turn_id ?? undefined)"
        >
          {{ ACTION_META[notice.resource].btn }}
        </button>
      </div>
      <details v-if="notice.detail" class="sys-more">
        <summary class="sys-line">
          <span class="sys-text">{{ notice.detailLabel || '展开详情' }}</span>
          <v-icon class="sys-chev" size="14">mdi-chevron-right</v-icon>
        </summary>
        <div v-if="notice.resource === 'doc'" class="doc-edit-diff" aria-label="文档修改对比">
          <template v-for="(line, index) in parseDiffLines(notice.detail)" :key="index">
            <div
              v-if="(line.kind === 'add' || line.kind === 'del') && docDiffText(line.text)"
              class="doc-edit-line"
              :class="`doc-edit-line--${line.kind}`"
              :aria-label="line.kind === 'add' ? '新增' : line.kind === 'del' ? '删除' : undefined"
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
    <!-- 后端报错 (backend_log.py): 芝士 needs the whole traceback, a
     person needs to know it happened. So the line shows by default
     and the stack is one click away — a room is a conversation, not
     a monitoring dashboard. -->
    <details
      v-else-if="notice.mode === 'backend-error'"
      class="sys-row sys-row--warn backend-error"
      data-testid="backend-error-event"
    >
      <summary class="sys-line">
        <span class="sys-text sys-lead">{{ notice.error.line }}</span>
        <v-icon class="sys-chev" size="14">mdi-chevron-right</v-icon>
        <span v-if="notice.error.count" class="sys-num">×<RollingNumber :value="notice.error.count" /></span>
      </summary>
      <div class="sys-fold">
        <div v-if="notice.error.where || notice.error.requestId" class="sys-meta">
          <span v-if="notice.error.where">{{ notice.error.where }}</span>
          <span v-if="notice.error.requestId"> req {{ notice.error.requestId }} </span>
        </div>
        <pre v-if="notice.error.stack" class="sys-detail">{{ notice.error.stack }}</pre>
      </div>
    </details>
    <CloudStartupStatus v-else-if="notice.mode === 'agent-status'" :events="run" />
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
        <span class="sys-text" :class="{ 'sys-lead': notice.who === 'human' }">{{ notice.line }}</span>
        <v-icon class="sys-chev" size="14">mdi-chevron-right</v-icon>
        <span v-if="notice.count > 1" class="sys-num">×<RollingNumber :value="notice.count" /></span>
        <span v-if="notice.whoLabel" class="sys-who">{{
          notice.who === 'cheese' ? `${name || agentName}正在处理` : notice.whoLabel
        }}</span>
        <!-- 在 summary 里点它不能顺带展开这一行。 -->
        <button v-if="showRetry" type="button" class="sys-btn" :disabled="retrying" @click.prevent.stop="emit('retry')">
          {{ t('work.room.retry.action') }}
        </button>
      </summary>
      <div class="sys-fold">
        <div v-for="(occ, oi) in notice.occurrences" :key="oi" class="sys-occurrence">
          <div class="sys-meta">
            {{ occ.label || '详情' }}<template v-if="notice.count > 1"> · {{ occ.line }}</template>
          </div>
          <pre v-if="occ.detail" class="sys-detail">{{ occ.detail }}</pre>
        </div>
      </div>
    </details>
    <div v-else-if="notice.mode === 'plain'" class="sys-row im-event">
      <div class="sys-line">
        <span class="sys-text" v-html="renderPlain(block.content)" />
      </div>
    </div>
  </AgentNoticeFrame>
</template>

<style scoped>
/* ---------------------------------------------------------------------------
   事件行里装的东西。几何（记号列、正文轴、行尾时间）归外框 AgentNoticeFrame，
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
/* 轻重只改底色和状态字的颜色。底色块往左多出 8px，字仍在正文那条竖线上。 */
.sys-row--warn,
.sys-row--danger {
  margin-left: -8px;
  padding: 4px 8px;
  border-radius: var(--radius-md);
}
.sys-row--warn {
  background: var(--warn-wash);
}
.sys-row--warn .sys-who {
  color: var(--warn-ink);
}
.sys-row--danger {
  background: var(--danger-wash);
}
.sys-row--danger .sys-who {
  color: var(--danger-ink);
}

/* 房间里发生的事：谁都没做这件事，所以它不进头像列，也不上正文轴，居中一行淡字。 */
.room-happening {
  margin: 10px 16px;
  text-align: center;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--faint);
  overflow-wrap: anywhere;
}
.room-happening :deep(.mention) {
  color: var(--muted);
  cursor: pointer;
}
.room-happening :deep(.mention:hover) {
  text-decoration: underline;
}
</style>
