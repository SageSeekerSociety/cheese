<script setup lang="ts">
// 一次评测的结果（方案 §看结果、§批改窗口）：顶上两个数（测试点通过几个、折算多少分），
// 下面逐个用例的状态、耗时、内存、用例分。
//
// 谁看到什么由后端裁好（`judge_queue.run_to_api`）：能批改的人拿到每个用例的输入、期望
// 和实际输出，点开就看；学生的隐藏用例只有状态和耗时，不能点开；学生的样例拿到实际输
// 出，输入和期望从题目的测试点里对上（`samples`，同一版测试点时按位置对）。
//
// 出题人的试跑和学生提交用的是同一个组件：试跑多报一句最慢的用例离时限还有多少。
import type { TaskJudgeCase, TaskJudgeRun, TaskTestCase } from '@/types'

import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'
import { renderNoticeMessage } from '@/lib/noticeText'

const props = withDefaults(
  defineProps<{
    run: TaskJudgeRun
    /** 能批改的人：每个用例都能点开。 */
    teacher?: boolean
    /** 测试点那一行的分值：给了就报「折算成多少分」。 */
    linePoints?: number | null
    /** 题目上那一版测试点的用例（学生读到的，隐藏用例没有输入输出）。 */
    samples?: TaskTestCase[]
    /** 题目上那一版测试点的版本号：和这次评测用的一致才按位置对样例。 */
    suiteRevision?: number | null
    /** 试跑：最慢的用例和时限（毫秒）。 */
    slowestMs?: number | null
    timeLimitMs?: number | null
  }>(),
  {
    teacher: false,
    linePoints: null,
    samples: () => [],
    suiteRevision: null,
    slowestMs: null,
    timeLimitMs: null,
  }
)

const { t } = useI18n()

const cases = computed(() => props.run.cases ?? [])
const passed = computed(() => props.run.passedCases ?? cases.value.filter((c) => c.status === 'ok').length)
const count = computed(() => props.run.caseCount ?? cases.value.length)

/** 测试点那一行折算的分：通过分值 ÷ 总分值 × 行分值，四舍五入（后端 `auto_score`）。 */
const lineScore = computed(() => {
  if (props.linePoints == null || !props.run.totalPoints) return null
  return Math.round((props.run.passedPoints / props.run.totalPoints) * props.linePoints)
})

const errorText = computed(() =>
  props.run.error ? renderNoticeMessage(props.run.error, t('tasks.judge.result.failedUnknown')) : ''
)

const sampleFor = (index: number): TaskTestCase | undefined =>
  props.suiteRevision != null && props.suiteRevision === props.run.suiteRevision ? props.samples[index] : undefined

/** 这个用例点不点得开：老师都能；学生只能点开样例。 */
const canOpen = (entry: TaskJudgeCase) => props.teacher || entry.visibility === 'SAMPLE'

const open = ref<Set<number>>(new Set())
function toggle(index: number) {
  const next = new Set(open.value)
  if (next.has(index)) next.delete(index)
  else next.add(index)
  open.value = next
}

const statusKey = (status: string) =>
  ['ok', 'wrong', 'timeout', 'memory', 'runtime_error', 'output_limit', 'compile_error'].includes(status)
    ? status
    : 'unknown'

function inputOf(entry: TaskJudgeCase, index: number) {
  return entry.input ?? sampleFor(index)?.input ?? null
}
function expectedOf(entry: TaskJudgeCase, index: number) {
  return entry.expected ?? sampleFor(index)?.expected ?? null
}

/** 期望和实际输出第一处不一样的那一行（从 1 数），一样就是 null。只看行末空白以外的内容。 */
function firstDiff(expected: string | null, actual: string | null | undefined): number | null {
  if (expected == null || actual == null) return null
  const a = expected.replace(/\s+$/, '').split('\n')
  const b = actual.replace(/\s+$/, '').split('\n')
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    if ((a[i] ?? '').trimEnd() !== (b[i] ?? '').trimEnd()) return i + 1
  }
  return null
}

const margin = computed(() =>
  props.slowestMs != null && props.timeLimitMs != null ? props.timeLimitMs - props.slowestMs : null
)
</script>

<template>
  <div class="tjr" data-testid="task-judge-result">
    <p v-if="run.state === 'queued' || run.state === 'running'" class="tjr__pending" data-testid="task-judge-pending">
      <v-progress-circular indeterminate size="16" width="2" />
      {{ run.state === 'queued' ? t('tasks.judge.result.queued') : t('tasks.judge.result.running') }}
    </p>

    <p v-else-if="run.state === 'failed'" class="tjr__failed" role="status" data-testid="task-judge-failed">
      {{ errorText }}
    </p>

    <template v-else>
      <div class="tjr__head">
        <div class="tjr__stat">
          <span class="tjr__big t-num" data-testid="task-judge-passed">{{ passed }}/{{ count }}</span>
          <span class="t-meta-read">{{
            t('tasks.judge.result.casesPassed', { points: run.passedPoints, total: run.totalPoints })
          }}</span>
        </div>
        <div v-if="lineScore != null" class="tjr__stat">
          <span class="tjr__big t-num" data-testid="task-judge-line-score">{{ lineScore }}/{{ linePoints }}</span>
          <span class="t-meta-read">{{ t('tasks.judge.result.lineScore') }}</span>
        </div>
        <slot name="aside" />
      </div>

      <p v-if="margin != null" class="tjr__margin t-meta-read" data-testid="task-judge-margin">
        {{
          margin >= 0
            ? t('tasks.judge.result.margin', { slowest: slowestMs, limit: timeLimitMs, left: margin })
            : t('tasks.judge.result.overLimit', { slowest: slowestMs, limit: timeLimitMs })
        }}
      </p>

      <div v-if="run.compileOutput" class="tjr__compile">
        <span class="tjr__label">{{ t('tasks.judge.result.compileOutput') }}</span>
        <pre class="tjr__pre">{{ run.compileOutput }}</pre>
      </div>

      <ol class="tjr__cases">
        <li v-for="(entry, index) in cases" :key="index" class="tjr__case" :data-status="entry.status">
          <div class="tjr__row">
            <BaseButton
              v-if="canOpen(entry)"
              kind="ghost"
              size="sm"
              :icon="open.has(index) ? 'mdi-chevron-down' : 'mdi-chevron-right'"
              :aria-expanded="open.has(index)"
              :aria-label="t('tasks.judge.result.toggle', { name: entry.name })"
              @click="toggle(index)"
            />
            <span v-else class="tjr__spacer" aria-hidden="true" />
            <span
              class="tjr__dot"
              :class="entry.status === 'ok' ? 'tjr__dot--ok' : 'tjr__dot--bad'"
              aria-hidden="true"
            />
            <span class="tjr__name" data-user-content>
              {{ entry.name }}
              <span v-if="entry.visibility === 'HIDDEN'" class="tjr__hidden t-meta-read">{{
                t('tasks.judge.result.hidden')
              }}</span>
            </span>
            <span class="tjr__status" data-testid="task-judge-case-status">{{
              t(`tasks.judge.status.${statusKey(entry.status)}`)
            }}</span>
            <span class="tjr__ms t-num t-meta-read">{{ entry.ms != null ? `${entry.ms} ms` : '—' }}</span>
            <span class="tjr__mem t-num t-meta-read">{{
              entry.memoryKb != null ? `${Math.round(entry.memoryKb / 1024)} MB` : '—'
            }}</span>
            <span class="tjr__pts t-num">{{ entry.status === 'ok' ? entry.points : 0 }}/{{ entry.points }}</span>
          </div>
          <div v-if="open.has(index)" class="tjr__detail">
            <div v-if="inputOf(entry, index) != null" class="tjr__io">
              <span class="tjr__label">{{ t('tasks.judge.result.input') }}</span>
              <pre class="tjr__pre">{{ inputOf(entry, index) }}</pre>
            </div>
            <div v-if="expectedOf(entry, index) != null" class="tjr__io">
              <span class="tjr__label">{{ t('tasks.judge.result.expected') }}</span>
              <pre class="tjr__pre">{{ expectedOf(entry, index) }}</pre>
            </div>
            <div v-if="entry.actual != null" class="tjr__io">
              <span class="tjr__label">{{ t('tasks.judge.result.actual') }}</span>
              <pre class="tjr__pre">{{ entry.actual }}</pre>
            </div>
            <p
              v-if="entry.status === 'wrong' && firstDiff(expectedOf(entry, index), entry.actual) != null"
              class="tjr__diff"
              data-testid="task-judge-diff"
            >
              {{ t('tasks.judge.result.firstDiff', { line: firstDiff(expectedOf(entry, index), entry.actual) }) }}
            </p>
            <div v-if="entry.stderr" class="tjr__io">
              <span class="tjr__label">{{ t('tasks.judge.result.stderr') }}</span>
              <pre class="tjr__pre">{{ entry.stderr }}</pre>
            </div>
          </div>
        </li>
      </ol>
    </template>
  </div>
</template>

<style scoped>
.tjr {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.tjr__pending,
.tjr__failed {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  color: var(--muted);
}

.tjr__failed {
  padding: 8px 12px;
  border-radius: var(--radius-sm);
  background: var(--warn-wash);
  color: var(--warn-ink);
}

.tjr__head {
  display: flex;
  align-items: flex-end;
  gap: 24px;
  flex-wrap: wrap;
}

.tjr__stat {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.tjr__big {
  color: var(--ink);
  font-size: 22px;
  font-weight: 600;
  line-height: 1.2;
}

.tjr__margin {
  margin: 0;
}

.tjr__label {
  color: var(--muted);
  font-size: 12px;
}

.tjr__pre {
  max-height: 240px;
  margin: 4px 0 0;
  padding: 8px;
  overflow: auto;
  border-radius: var(--radius-sm);
  background: var(--fill-2);
  color: var(--ink);
  font-family: var(--font-mono);
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
}

.tjr__cases {
  display: flex;
  flex-direction: column;
  margin: 0;
  padding: 0;
  list-style: none;
  border-top: 1px solid var(--line-2);
}

.tjr__case {
  border-bottom: 1px solid var(--line-2);
}

.tjr__row {
  display: grid;
  grid-template-columns: 32px 8px minmax(0, 1fr) auto 64px 56px 56px;
  gap: 8px;
  align-items: center;
  min-height: 40px;
}

.tjr__spacer {
  width: 32px;
}

.tjr__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
}

.tjr__dot--ok {
  background: var(--ok);
}

.tjr__dot--bad {
  background: var(--danger);
}

.tjr__name {
  overflow: hidden;
  color: var(--ink);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tjr__hidden {
  margin-left: 6px;
}

.tjr__case[data-status='ok'] .tjr__status {
  color: var(--ok-ink);
}

.tjr__case:not([data-status='ok']) .tjr__status {
  color: var(--danger-ink);
}

.tjr__status {
  font-size: 13px;
}

.tjr__ms,
.tjr__mem,
.tjr__pts {
  text-align: right;
}

.tjr__detail {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 12px;
  padding: 0 0 12px 40px;
}

.tjr__diff {
  grid-column: 1 / -1;
  margin: 0;
  color: var(--danger-ink);
  font-size: 12px;
}

@media (max-width: 599px) {
  .tjr__row {
    grid-template-columns: 32px 8px minmax(0, 1fr) auto 56px;
  }

  .tjr__mem,
  .tjr__ms {
    display: none;
  }

  .tjr__detail {
    padding-left: 0;
  }
}
</style>
