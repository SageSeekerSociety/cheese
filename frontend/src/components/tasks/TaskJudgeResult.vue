<script setup lang="ts">
// 一次评测的结果（方案 §看结果、§批改窗口）：顶上两个数（测试点通过几个、折算多少分），
// 下面逐个用例的状态、耗时、内存、用例分。
//
// 谁看到什么由后端裁好（`judge_queue.run_to_api`）：能批改的人拿到每个用例的输入、期望
// 和实际输出，点开就看；学生的隐藏用例只有状态和耗时，不能点开；学生的样例拿到实际输
// 出，输入和期望从题目的测试点里对上（`samples`，同一版测试点时按位置对）。
//
// 出题人的试跑和学生提交用的是同一个组件：试跑多报一句最慢的用例离时限还有多少。
//
// 三种形态（`kind`，不给就从结果行认：带 `maxScore` 的是命令判分，带 `testId` 的是测试
// 脚本）：
// - 测试脚本：一行一个测试，没有输入和期望输出；有失败信息的那一行能点开看（学生只拿到样例
//   那几条的信息）；跳过的、出题人没配分的另外标出来。
// - 命令判分：顶上是判分命令给的分 x/y，下面是 score.json 里的各项；不公开的那几项学生那边
//   根本没有，只说还有几分来自不公开的检查项。
// - 判分失败（`scoreFailed`：报告或分数读不出来）：不按 0 分算。老师看程序输出前 200 行
//   手动给分，学生看出题人允许的前几行（`studentOutput`）。
import type { TaskJudgeCase, TaskJudgeRun, TaskTestCase, TaskTestSuiteKind } from '@/types'

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
    /** 这套测试点的形态；不给就从结果行认。 */
    kind?: TaskTestSuiteKind | null
  }>(),
  {
    kind: null,
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

/** 结果是哪一种形态：给了就用，没给就从结果行认。 */
const mode = computed<TaskTestSuiteKind>(() => {
  if (props.kind) return props.kind
  if (cases.value.some((c) => c.maxScore != null)) return 'COMMAND'
  if (cases.value.some((c) => c.testId)) return 'SCRIPT'
  return 'IO'
})

const fractional = (n: number | null | undefined) => n != null && !Number.isInteger(n)
/** 命令判分的分：两个数都是整数时后端原样记，带小数时都乘了 100（7.5/10 记成 750/1000）。
 *  哪一项带小数就说明乘过，除回来。 */
const commandScale = computed(() => (cases.value.some((c) => fractional(c.score) || fractional(c.maxScore)) ? 100 : 1))
const fmt = (n: number) => String(Math.round(n * 100) / 100)
const commandScore = computed(() => ({
  got: fmt(props.run.passedPoints / commandScale.value),
  top: fmt(props.run.totalPoints / commandScale.value),
}))
/** 学生那边看不到的检查项合起来几分（不公开的那几行学生拿不到）。 */
const hiddenPoints = computed(() => {
  if (props.teacher || mode.value !== 'COMMAND') return 0
  const shown = cases.value.reduce((sum, c) => sum + (c.maxScore ?? 0), 0)
  const left = props.run.totalPoints / commandScale.value - shown
  return left > 0.005 ? fmt(left) : 0
})

/** 程序输出：老师看前 200 行（`compileOutput`），学生看出题人允许的那几行。IO 的编译输出
 *  照旧放在「编译输出」下。 */
const programOutput = computed(() =>
  mode.value === 'IO' ? null : props.teacher ? props.run.compileOutput ?? null : props.run.studentOutput ?? null
)
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

/** 这个用例点不点得开：老师都能，学生只能点开样例；测试脚本只有带失败信息的能点开，
 *  命令判分的各项没有可展开的东西。 */
const canOpen = (entry: TaskJudgeCase) => {
  if (mode.value === 'COMMAND') return false
  if (mode.value === 'SCRIPT') return !!entry.message
  return props.teacher || entry.visibility === 'SAMPLE'
}

const open = ref<Set<number>>(new Set())
function toggle(index: number) {
  const next = new Set(open.value)
  if (next.has(index)) next.delete(index)
  else next.add(index)
  open.value = next
}

const statusKey = (status: string) =>
  ['ok', 'wrong', 'timeout', 'memory', 'runtime_error', 'output_limit', 'compile_error', 'missing'].includes(status)
    ? status
    : 'unknown'

/** 一行的状态那一格：测试脚本里跳过的测试记成没过，这里说「跳过」。 */
const statusText = (entry: TaskJudgeCase) =>
  entry.skipped ? t('tasks.judge.result.skipped') : t(`tasks.judge.status.${statusKey(entry.status)}`)

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

    <template v-else-if="run.scoreFailed">
      <div class="tjr__failed tjr__failed--block" role="status" data-testid="task-judge-score-failed">
        <strong>{{ t('tasks.judge.result.scoreFailedTitle') }}</strong>
        <span>{{ errorText }}</span>
        <span>{{
          teacher ? t('tasks.judge.result.scoreFailedTeacher') : t('tasks.judge.result.scoreFailedStudent')
        }}</span>
      </div>
      <div v-if="teacher ? run.compileOutput : run.studentOutput" class="tjr__compile">
        <span class="tjr__label">{{
          teacher ? t('tasks.judge.result.programOutput') : t('tasks.judge.result.studentOutput')
        }}</span>
        <pre class="tjr__pre" data-testid="task-judge-output">{{
          teacher ? run.compileOutput : run.studentOutput
        }}</pre>
      </div>
    </template>

    <template v-else>
      <div class="tjr__head">
        <div v-if="mode === 'COMMAND'" class="tjr__stat">
          <span class="tjr__big t-num" data-testid="task-judge-score"
            >{{ commandScore.got }}/{{ commandScore.top }}</span
          >
          <span class="t-meta-read">{{ t('tasks.judge.result.score') }}</span>
        </div>
        <div v-else class="tjr__stat">
          <span class="tjr__big t-num" data-testid="task-judge-passed">{{ passed }}/{{ count }}</span>
          <span class="t-meta-read">{{
            t(mode === 'SCRIPT' ? 'tasks.judge.result.testsPassed' : 'tasks.judge.result.casesPassed', {
              points: run.passedPoints,
              total: run.totalPoints,
            })
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

      <div v-if="mode === 'IO' && run.compileOutput" class="tjr__compile">
        <span class="tjr__label">{{ t('tasks.judge.result.compileOutput') }}</span>
        <pre class="tjr__pre">{{ run.compileOutput }}</pre>
      </div>
      <div v-else-if="programOutput" class="tjr__compile">
        <span class="tjr__label">{{
          teacher ? t('tasks.judge.result.programOutput') : t('tasks.judge.result.studentOutput')
        }}</span>
        <pre class="tjr__pre" data-testid="task-judge-output">{{ programOutput }}</pre>
      </div>

      <ol class="tjr__cases" :data-kind="mode">
        <li v-for="(entry, index) in cases" :key="index" class="tjr__case" :data-status="entry.status">
          <div class="tjr__row" :class="{ 'tjr__row--command': mode === 'COMMAND' }">
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
              <span v-if="entry.visibility === 'HIDDEN' && mode !== 'COMMAND'" class="tjr__hidden t-meta-read">{{
                t('tasks.judge.result.hidden')
              }}</span>
              <span v-if="entry.private" class="tjr__hidden t-meta-read">{{ t('tasks.judge.result.private') }}</span>
              <span v-if="entry.unscored" class="tjr__hidden t-meta-read">{{ t('tasks.judge.result.unscored') }}</span>
              <span
                v-if="mode === 'SCRIPT' && entry.testId && entry.testId !== entry.name"
                class="tjr__test-id t-meta-read"
                >{{ entry.testId }}</span
              >
            </span>
            <template v-if="mode === 'COMMAND'">
              <span class="tjr__pts t-num" data-testid="task-judge-item-score"
                >{{ fmt(entry.score ?? 0) }}/{{ fmt(entry.maxScore ?? entry.points) }}</span
              >
            </template>
            <template v-else>
              <span class="tjr__status" data-testid="task-judge-case-status">{{ statusText(entry) }}</span>
              <span class="tjr__ms t-num t-meta-read">{{ entry.ms != null ? `${entry.ms} ms` : '—' }}</span>
              <span class="tjr__mem t-num t-meta-read">{{
                entry.memoryKb != null ? `${Math.round(entry.memoryKb / 1024)} MB` : '—'
              }}</span>
              <span class="tjr__pts t-num">{{ entry.status === 'ok' ? entry.points : 0 }}/{{ entry.points }}</span>
            </template>
          </div>
          <div v-if="open.has(index) && mode === 'SCRIPT'" class="tjr__detail">
            <div class="tjr__io">
              <span class="tjr__label">{{ t('tasks.judge.result.message') }}</span>
              <pre class="tjr__pre" data-testid="task-judge-message">{{ entry.message }}</pre>
            </div>
          </div>
          <div v-else-if="open.has(index)" class="tjr__detail">
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
      <p v-if="hiddenPoints" class="tjr__margin t-meta-read" data-testid="task-judge-hidden-items">
        {{ t('tasks.judge.result.hiddenItems', { n: hiddenPoints }) }}
      </p>
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

.tjr__failed--block {
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
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

.tjr__row--command {
  grid-template-columns: 32px 8px minmax(0, 1fr) auto;
}

.tjr__test-id {
  margin-left: 6px;
  font-family: var(--font-mono);
  font-size: 12px;
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

  .tjr__row--command {
    grid-template-columns: 32px 8px minmax(0, 1fr) auto;
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
