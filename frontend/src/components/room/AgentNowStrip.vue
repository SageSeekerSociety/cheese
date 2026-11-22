<script setup lang="ts">
// 频道、支线页顶部那一块「AI 现在在干什么」：每位在干活的队友一行 —— 图标、名字、一句话。
// 进页面就能看到，不用滚到输入框上面那一行。一句话取队友此刻的那一步（live 帧），没有就取
// 时间线上的阶段，再没有就说「正在工作」；图标和输入框上方那一行是同一套：在跑＝绿色旋转圈，
// 很久没新动静＝不转的空心环加橙点，平台在等自动重试＝黄色警示。悬停出详情。
// 只凭 props 画；没人在干活时这一块不存在。
import type { AgentFailureLine } from '@/lib/agentFailures'
import type { MemberActivityLine } from '@/lib/memberActivity'

import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { t } from '@/i18n'
import { needsRelogin } from '@/lib/agentFailures'
import { lineStale } from '@/lib/memberActivity'
import { waitedFor } from '@/lib/replyWait'
import { formatSpan } from '@/lib/siteLog'
import { STALE_AFTER_MS } from '@/lib/taskState'

const props = withDefaults(defineProps<{ lines: MemberActivityLine[]; failures?: AgentFailureLine[] }>(), {
  failures: () => [],
})
const emit = defineEmits<{ (e: 'relogin', handle: string): void }>()

const working = computed(() => props.lines.filter((l) => l.kind !== 'typing'))

const now = ref(Date.now())
let ticker: ReturnType<typeof setInterval> | undefined
watch(
  () => working.value.length + props.failures.length > 0,
  (on) => {
    clearInterval(ticker)
    now.value = Date.now()
    ticker = on ? setInterval(() => (now.value = Date.now()), 30_000) : undefined
  },
  { immediate: true }
)
onBeforeUnmount(() => clearInterval(ticker))

function kindOf(f: AgentFailureLine): 'Login' | 'Model' | 'Other' {
  return f.code === 'login_expired' ? 'Login' : f.code === 'model_error' ? 'Model' : 'Other'
}
function failureText(f: AgentFailureLine): string {
  return t(`work.room.activity.failure.failed${kindOf(f)}`, { time: waitedFor(f.since, now.value) })
}
function failureTitle(f: AgentFailureLine): string {
  const k = kindOf(f)
  return [
    t(`work.room.activity.failure.reason${k}`),
    f.name,
    waitedFor(f.since, now.value),
    t(`work.room.activity.failure.hint${k}`),
  ]
    .filter(Boolean)
    .join('\n')
}

function stale(line: MemberActivityLine): boolean {
  return lineStale(line, now.value, STALE_AFTER_MS)
}

/** 上一轮留下的帧（比这一轮还早）不是这一轮的当前一步。 */
function step(line: MemberActivityLine): string {
  const fresh = line.lastFrameAt != null && line.lastFrameAt < line.since * 1000 ? null : line.step
  return fresh ?? line.detail ?? ''
}

function sentence(line: MemberActivityLine): string {
  if (line.kind === 'retrying') return t('work.room.activity.retryingShort')
  return step(line) || t('work.room.activity.workingShort')
}

function since(line: MemberActivityLine): string {
  return line.since ? formatSpan(Math.max(0, Math.floor((now.value - line.since * 1000) / 1000))) : ''
}

function title(line: MemberActivityLine): string {
  const head = stale(line)
    ? t('work.taskState.detail.staleTitle')
    : line.kind === 'retrying'
      ? t('work.room.activity.retryingShort')
      : t('work.room.activity.working', { name: line.name })
  return [head, step(line), t('work.taskState.detail.sessionLive'), since(line)].filter(Boolean).join('\n')
}
</script>

<template>
  <div v-if="working.length || failures.length" class="agent-now" data-testid="agent-now" role="status">
    <div
      v-for="f in failures"
      :key="`failed-${f.handle}`"
      class="agent-now__row agent-now__row--failed"
      data-kind="failed"
      :data-code="f.code ?? ''"
      :title="failureTitle(f)"
    >
      <span class="agent-now__mark agent-now__mark--failed" aria-hidden="true">
        <i
          class="mdi agent-now__glyph"
          :class="f.code === 'model_error' ? 'mdi-alert-circle' : 'mdi-cloud-off-outline'"
        />
      </span>
      <span class="agent-now__name">{{ f.name }}</span>
      <span class="agent-now__text agent-now__text--failed">{{ failureText(f) }}</span>
      <button
        v-if="needsRelogin(f)"
        type="button"
        class="agent-now__action"
        data-testid="agent-now-relogin"
        @click="emit('relogin', f.handle)"
      >
        {{ t('work.room.activity.failure.relogin') }}
      </button>
    </div>
    <div
      v-for="line in working"
      :key="line.handle"
      class="agent-now__row"
      :class="{ 'agent-now__row--stale': stale(line) }"
      :data-kind="line.kind"
      :title="title(line)"
    >
      <span class="agent-now__mark" :class="{ 'agent-now__mark--stale': stale(line) }" aria-hidden="true">
        <i
          class="mdi agent-now__glyph"
          :class="line.kind === 'retrying' ? 'mdi-alert-circle' : stale(line) ? 'mdi-circle-outline' : 'mdi-timelapse'"
        />
        <span v-if="stale(line)" class="agent-now__dot" />
      </span>
      <span class="agent-now__name">{{ line.name }}</span>
      <span class="agent-now__text">{{ sentence(line) }}</span>
      <span v-if="since(line)" class="agent-now__meta">{{ since(line) }}</span>
    </div>
  </div>
</template>

<style scoped>
.agent-now {
  flex: none;
  display: flex;
  flex-direction: column;
  margin: 8px 12px 0;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md, 10px);
  background: var(--surface);
  font-size: 13px;
  line-height: var(--lh-13);
}
.agent-now__row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  padding: 6px 12px;
  border-bottom: 1px solid var(--line);
  color: var(--text);
}
.agent-now__row:last-child {
  border-bottom: 0;
}
.agent-now__row--stale {
  background: var(--warn-wash);
}
.agent-now__mark {
  position: relative;
  flex: none;
  display: inline-flex;
  color: var(--ok);
}
.agent-now__row[data-kind='retrying'] .agent-now__mark {
  color: var(--warn);
}
@media (prefers-reduced-motion: no-preference) {
  .agent-now__mark:not(.agent-now__mark--stale) .mdi-timelapse {
    animation: agent-now-turn 2.4s linear infinite;
  }
}
@keyframes agent-now-turn {
  to {
    transform: rotate(360deg);
  }
}
.agent-now__glyph {
  font-size: 15px;
  line-height: 1;
}
.agent-now__dot {
  position: absolute;
  top: -2px;
  right: -3px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--warn);
  box-shadow: 0 0 0 1.5px var(--surface);
}
.agent-now__row--failed {
  background: var(--danger-wash);
}
.agent-now__mark--failed {
  color: var(--danger);
}
.agent-now__text--failed {
  color: var(--danger-ink);
}
.agent-now__action {
  flex: none;
  padding: 2px 10px;
  border: 0;
  border-radius: var(--radius-sm, 6px);
  background: var(--accent);
  color: var(--accent-on);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}
.agent-now__name {
  flex: none;
  max-width: 40%;
  overflow: hidden;
  font-weight: 600;
  color: var(--ink);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.agent-now__text {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.agent-now__meta {
  flex: none;
  color: var(--faint);
}
</style>
