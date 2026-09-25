<script setup lang="ts">
// 平台替这个房间写下的一条：出了什么事、谁在处理、详情在一次点击之后。
//
// 一条通知和一条消息长得不一样是有意的——它不是谁说的话，是这个房间发生的事。
// 六种档位共用同一个外框（`AgentNoticeFrame`），差别只在那一行里装什么。
//
// 这里只画已经判好档的东西：判档在 lib/platformNotice.ts，署名和时间由房间算好
// 传进来。它不认识名册，也不认识 socket。
import type { Block } from '../../cx_types'
import type { PlatformNotice } from '../../lib/platformNotice'

import { computed } from 'vue'

import { parseDiffLines } from '../../lib/diff'
import { renderPlain as renderPlainWith } from '../../lib/renderMessage'
import AgentNoticeFrame from '../AgentNoticeFrame.vue'
import CloudStartupStatus from '../CloudStartupStatus.vue'

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
  <AgentNoticeFrame :name="name" :time="time">
    <div
      v-if="notice.mode === 'incident'"
      class="sys-row sys-row--danger platform-incident"
      role="alert"
      :data-error-code="notice.incident.code"
      data-testid="platform-error-card"
    >
      <div class="sys-line">
        <v-icon class="sys-mark" :icon="notice.incident.icon" size="15" />
        <span class="sys-text">{{ notice.incident.title }}</span>
        <span class="sys-who">{{ notice.incident.status }}</span>
        <button v-if="showRetry" type="button" class="sys-action" :disabled="retrying" @click="emit('retry')">
          {{ t('work.room.retry.action') }}
        </button>
      </div>
      <div class="sys-sub">{{ notice.lead }}</div>
      <details v-if="notice.rest" class="sys-more">
        <summary>{{ notice.detailLabel || '展开详情' }}</summary>
        <pre class="sys-detail">{{ notice.rest }}</pre>
      </details>
    </div>
    <!-- 本轮摘要 (spec §8.5 变更提醒): 这一轮改了什么 + 顺带更新了什么。
     「查看改动」是这一行唯一的动作 —— 采纳是话题级的一次性动作，不是
     每轮都问一遍的东西（§14.6）。 -->
    <div v-else-if="notice.mode === 'turn-summary'" class="sys-row turn-summary">
      <div class="sys-line">
        <span class="sys-mark sys-mark--dot" aria-hidden="true" />
        <span class="sys-text">
          <template v-if="notice.changes">
            改动了 {{ notice.changes.filesTotal }} 个文件（+{{ notice.changes.added }} −{{ notice.changes.removed }}）
          </template>
          <template v-for="(act, ai) in notice.actions" :key="ai">
            <span v-if="ai > 0 || notice.changes" class="sys-sep"> · </span>
            <span v-html="renderPlain(act.text)" />
          </template>
        </span>
        <button
          v-if="notice.changes"
          type="button"
          class="sys-action"
          @click="emit('open-resource', 'changes', notice.turnId ?? undefined)"
        >
          查看改动
        </button>
      </div>
      <div v-if="notice.changes?.files.length" class="sys-sub sys-files">
        {{ notice.changes.files.join(' · ')
        }}<template v-if="notice.changes.filesOmitted"> · 另 {{ notice.changes.filesOmitted }} 个</template>
      </div>
    </div>
    <!-- 芝士这轮改了平台上的什么东西（没能折进本轮摘要的那一条） -->
    <div v-else-if="notice.mode === 'action'" class="sys-row action-card">
      <div class="sys-line">
        <span class="sys-mark sys-mark--dot" aria-hidden="true" />
        <!-- notice.text may carry a <@handle> actor token (编辑了文档): render
         through the shared token→chip path so the actor is clickable. -->
        <span class="sys-text" v-html="renderPlain(notice.text)" />
        <button
          v-if="ACTION_META[notice.resource]?.btn"
          type="button"
          class="sys-action"
          @click="emit('open-resource', notice.resource, block.turn_id ?? undefined)"
        >
          {{ ACTION_META[notice.resource].btn }}
        </button>
      </div>
      <details v-if="notice.detail" class="sys-more">
        <summary>{{ notice.detailLabel || '展开详情' }}</summary>
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
        <span class="sys-mark sys-mark--dot" aria-hidden="true" />
        <span class="sys-text">{{ notice.error.line }}</span>
        <span v-if="notice.error.count" class="sys-count">×{{ notice.error.count }}</span>
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
        <span class="sys-mark sys-mark--dot" aria-hidden="true" />
        <span class="sys-text">{{ notice.line }}</span>
        <span v-if="notice.count > 1" class="sys-count">×{{ notice.count }}</span>
        <span v-if="notice.whoLabel" class="sys-who">{{
          notice.who === 'cheese' ? `${name || agentName}正在处理` : notice.whoLabel
        }}</span>
        <!-- 在 summary 里点它不能顺带展开这一行。 -->
        <button
          v-if="showRetry"
          type="button"
          class="sys-action"
          :disabled="retrying"
          @click.prevent.stop="emit('retry')"
        >
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
    <!-- system / event blocks. Content may carry a <@handle> actor token
     (归档/编辑…): render it through the SAME token→chip path as
     messages so the actor is a clickable mention, not raw text. -->
    <div v-else-if="notice.mode === 'plain'" class="sys-row im-event">
      <div class="sys-line">
        <span class="sys-mark sys-mark--dot" aria-hidden="true" />
        <span class="sys-text" v-html="renderPlain(block.content)" />
      </div>
    </div>
  </AgentNoticeFrame>
</template>

<style scoped>
/* ---------------------------------------------------------------------------
   平台行 —— 时间线上除了「人说的话」以外的一切，共用这一种形态。
   之前这里有五套几何：居中淡行、居中动作行、16px 起的折叠卡、16px 起的报错卡、
   还有一张 12px 圆角带渐变的事故卡。同一列里三条不同的左边缘（16 / 54 / 居中）
   是它读起来乱的直接原因。现在只有一条轴：和消息正文对齐的 54px
   （.im-row 的 16px padding + 28px 头像槽 + 10px gap）。
   严重度只改**记号和底色**，绝不改形态。
   --------------------------------------------------------------------------- */
.sys-row {
  padding: 3px 16px 3px 54px;
  font-size: 13px; /* 13px 是可读下限；平台行比正文低一档，不低于它 */
  line-height: var(--lh-13);
  color: var(--muted);
}
/* 分栏之下，「谁都没说这句话」需要自己的位置：一行字的平台行居中（飞书/微信
   的通行做法）。**只有单行的那两种**——带右侧归属/状态列的动作卡、可折叠的
   报错卡、事故卡仍然留在左轴上：把一张右侧有状态列的卡居中，那一列就没了落点。 */
.sys-row.turn-summary,
.sys-row.im-event {
  padding-left: 16px;
}
.sys-row.turn-summary .sys-line,
.sys-row.im-event .sys-line {
  justify-content: center;
}
.sys-row.turn-summary .sys-text,
.sys-row.im-event .sys-text {
  flex: 0 1 auto;
}
details.sys-row > summary {
  cursor: pointer;
  list-style: none;
}
details.sys-row > summary::-webkit-details-marker {
  display: none;
}
.sys-line {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}
/* 记号槽：宽度固定，所以圆点和图标不会把文字推成两个起点。 */
.sys-mark {
  flex: none;
  width: 15px;
  align-self: center;
  color: var(--faint);
}
.sys-mark--dot {
  position: relative;
  height: 15px;
}
.sys-mark--dot::before {
  content: '';
  position: absolute;
  top: 50%;
  left: 4px;
  width: 6px;
  height: 6px;
  margin-top: -3px;
  border-radius: 50%;
  background: currentcolor;
}
.sys-text {
  flex: 1 1 auto;
  min-width: 0;
  overflow-wrap: anywhere;
}
/* 右侧固定放「归属 / 状态」和「动作」——扫一列就知道有没有在等自己。 */
.sys-who {
  flex: none;
  font-size: 12px;
  color: var(--faint);
}
.sys-count {
  flex: none;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--faint);
}
.sys-action {
  flex: none;
  border: none;
  background: none;
  padding: 0;
  font-size: 13px;
  color: var(--accent-ink); /* 记号色做文字对比度不够，见 design-system §1.6 */
  cursor: pointer;
}
.sys-action:hover {
  text-decoration: underline;
}
.sys-sub {
  padding-left: 23px;
  color: var(--muted);
  overflow-wrap: anywhere;
}
.sys-fold,
.sys-more {
  padding-left: 23px;
}
.sys-more > summary {
  cursor: pointer;
  font-size: 12px;
  color: var(--faint);
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
/* 本轮摘要：文件清单是次要信息，压到元信息档，一行放不下就截断。 */
.sys-files {
  font-size: 12px;
  color: var(--faint);
  font-family: var(--font-mono);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sys-sep {
  color: var(--faint);
}
/* 两档色，底色一律取自色板的 -wash，不再手搓 color-mix。 */
.sys-row--warn {
  background: var(--warn-wash);
}
.sys-row--warn .sys-mark {
  color: var(--warn);
}
.sys-row--danger {
  background: var(--danger-wash);
}
.sys-row--danger .sys-mark {
  color: var(--danger);
}
.sys-row--warn,
.sys-row--danger {
  padding-top: 6px;
  padding-bottom: 6px;
}
</style>
