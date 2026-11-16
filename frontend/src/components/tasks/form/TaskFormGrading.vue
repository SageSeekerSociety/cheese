<script setup lang="ts">
// 「提交与评分」那一节：学生交哪几样东西（每样是文件还是文字），评分要点（评什么、
// 多少分、怎样算做到），以及评分要点给不给学生看。
//
// 它只画：三样值都是 props 进来的，改一次往上报一次。题目分了阶段（`stages`）时，提交
// 项和评分要点按阶段分组，每组下面加的那一项属于这个阶段；不分阶段时就是一组，没有
// 阶段的标题。空着的评分行在提交时由 `useTaskForm` 去掉；写了标准没写名称的那一行，外面
// 用 `missingName` 让它标红。
//
// 评分要点的一行由谁给分（`app/domain/task/rubric.py` 的 `LINE_KINDS`）：默认是老师
// 评审时手填（`manual`），也可以加一行额度用量（`usage`），由平台按这份领题用掉的额度
// 自动算分。「添加一项」是一张小菜单，两种行二选一；额度用量一道题最多一行（后端
// `rubric._check_automatic`），加过就禁用。含自动算分那一行的阶段合计要等于 100 分，
// 不然发布会被后端 400，这里先算出来、当场说出来。
import type {
  TaskRubricLine,
  TaskRubricLineConfig,
  TaskStage,
  TaskSubmissionEntryType,
  TaskSubmissionSchemaEntry,
} from '@/types'

import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import TaskFormSection from './TaskFormSection.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseField from '@/components/base/BaseField.vue'
import SegmentedControl from '@/components/common/SegmentedControl.vue'
import { settledStage } from '@/lib/taskStages'

/** 含自动算分行时，那个阶段的评分要点要到的合计（后端 `rubric.AUTO_TOTAL`）。 */
const AUTO_TOTAL = 100

const props = withDefaults(
  defineProps<{
    /** 点过发布后，有写了标准却没写名称的那一行。 */
    missingName?: boolean
    /** 题目的阶段；空 = 不分阶段。 */
    stages?: TaskStage[]
  }>(),
  { missingName: false, stages: () => [] }
)

const items = defineModel<TaskSubmissionSchemaEntry[]>('submissionSchema', { required: true })
const rubric = defineModel<TaskRubricLine[]>('rubric', { required: true })
const rubricPublic = defineModel<boolean>('rubricPublic', { required: true })

const { t } = useI18n()

const typeOptions = computed(() => [
  { value: 'FILE' as const, label: t('tasks.form.grading.typeFile') },
  { value: 'TEXT' as const, label: t('tasks.form.grading.typeText') },
])

const modeOptions = computed(() => [
  { value: 'threshold' as const, label: t('tasks.form.grading.modeThreshold') },
  { value: 'rank' as const, label: t('tasks.form.grading.modeRank') },
])

/** 额度那一行给的分：不超过 `full` 满分、到 `zero` 得 0 分，中间按比例四舍五入
 *  （`usage_score.threshold_score`）。 */
function usageScore(credits: number, points: number, full: number, zero: number): number {
  if (credits <= full) return points
  if (credits >= zero || zero <= full) return 0
  return Math.round((points * (zero - credits)) / (zero - full))
}

/** 这一行是不是平台自动算分的（额度用量）。 */
const isAuto = (line: TaskRubricLine) => (line.kind ?? 'manual') !== 'manual'

/** 每组：一个阶段（不分阶段时是唯一的那一组，没有标题，阶段是 null），和按阶段的 id
 *  归到它下面的提交项、评分要点在整份列表里的位置；手动行和自动行分开画。 */
const groups = computed(() => {
  const stages = props.stages.length ? props.stages : [null]
  return stages.map((stage, index) => {
    const id = stage?.id ?? null
    const lines = rubric.value
      .map((line, at) => ({ line, at }))
      .filter(({ line }) => settledStage(props.stages, line.stage) === id)
    return {
      key: id ?? 'none',
      stage: id,
      title: stage ? t('tasks.form.grading.stageTitle', { n: index + 1, name: stage.name || '' }) : null,
      items: items.value
        .map((entry, at) => ({ entry, at }))
        .filter(({ entry }) => settledStage(props.stages, entry.stage) === id),
      lines,
      manual: lines.filter(({ line }) => !isAuto(line)),
      auto: lines.filter(({ line }) => isAuto(line)),
      total: pointsOf(lines.map(({ line }) => line)),
      hasAuto: lines.some(({ line }) => isAuto(line)),
    }
  })
})

const staged = computed(() => props.stages.length > 0)

/** 一道题最多一行额度用量：整份评分要点里有一行就算加过了（后端 `rubric.rubricUsageOnce`）。 */
const hasUsage = computed(() => rubric.value.some(isAuto))

function setItem(index: number, patch: Partial<TaskSubmissionSchemaEntry>) {
  items.value = items.value.map((entry, i) => (i === index ? { ...entry, ...patch } : entry))
}
function addItem(stage: string | null) {
  items.value = [...items.value, { prompt: '', type: 'FILE', stage }]
}
function removeItem(index: number) {
  items.value = items.value.filter((_, i) => i !== index)
}

function setLine(index: number, patch: Partial<TaskRubricLine>) {
  rubric.value = rubric.value.map((line, i) => (i === index ? { ...line, ...patch } : line))
}
function addLine(stage: string | null) {
  rubric.value = [...rubric.value, { item: '', points: 0, criteria: '', stage }]
}
/** 加一行额度用量：名称由平台给（`rubric.USAGE_DEFAULT_NAME`），设置从一个能立刻改的
 *  起点开始（按阈值，0–100）。 */
function addUsageLine(stage: string | null) {
  if (hasUsage.value) return
  rubric.value = [
    ...rubric.value,
    { item: '', points: 0, criteria: '', stage, kind: 'usage', config: { mode: 'threshold', full: 0, zero: 100 } },
  ]
}
function setUsageConfig(index: number, patch: Partial<TaskRubricLineConfig>) {
  const line = rubric.value[index]
  if (!line) return
  setLine(index, { config: { mode: 'threshold', ...(line.config ?? {}), ...patch } })
}
function removeLine(index: number) {
  rubric.value = rubric.value.filter((_, i) => i !== index)
}

/** 一组评分要点的合计（每个阶段那一组在 `groups` 里算，这里是整份）。 */
function pointsOf(lines: TaskRubricLine[]): number {
  return lines.reduce((running, line) => running + (Number(line.points) || 0), 0)
}
const total = computed(() => pointsOf(rubric.value))

/** 额度用量在合计里占几成。 */
const usagePoints = computed(() => pointsOf(rubric.value.filter(isAuto)))
const usageShare = computed(() => (total.value ? Math.round((usagePoints.value / total.value) * 100) : 0))

const nameMissing = (line: TaskRubricLine) =>
  props.missingName && !isAuto(line) && !line.item.trim() && !!line.criteria.trim()

// ── 试算 ──────────────────────────────────────────────────────────────────────
/** 试算那一格按行记住：没填过就用两个阈值中间的那个数当起点，改一次记一次。 */
const tried = ref<Record<number, number>>({})
function tryCredit(at: number, full: number, zero: number): number {
  return tried.value[at] ?? Math.round((full + zero) / 2)
}
function setTryCredit(at: number, value: number) {
  tried.value = { ...tried.value, [at]: value }
}

/** 这一行的设置，缺的都补齐：画的时候不用到处判 `undefined`。 */
function usageConfig(line: TaskRubricLine): { mode: 'threshold' | 'rank'; full: number; zero: number } {
  return {
    mode: line.config?.mode ?? 'threshold',
    full: Number(line.config?.full) || 0,
    zero: Number(line.config?.zero) || 0,
  }
}

/** 那一行右侧的一句规则：按阈值说阈值，按排名说排名。 */
function ruleText(line: TaskRubricLine): string {
  const config = usageConfig(line)
  return config.mode === 'rank'
    ? t('tasks.form.grading.usageRuleRank')
    : t('tasks.form.grading.usageRuleThreshold', { full: config.full, zero: config.zero })
}
</script>

<template>
  <TaskFormSection :title="t('tasks.form.grading.title')">
    <BaseField data-agent-field="submission" :label="t('tasks.form.grading.items')">
      <div class="tfg__groups" data-testid="task-form-submission-items">
        <div v-for="group in groups" :key="group.key" class="tfg__items">
          <p v-if="group.title" class="tfg__stage t-eyebrow-read" data-user-content>{{ group.title }}</p>
          <div v-for="({ entry, at }, n) in group.items" :key="at" class="tfg__item">
            <span class="tfg__n t-num">{{ n + 1 }}</span>
            <v-text-field
              :model-value="entry.prompt"
              :aria-label="t('tasks.form.grading.itemName', { n: n + 1 })"
              :placeholder="t('tasks.form.grading.itemPlaceholder')"
              autocomplete="off"
              maxlength="255"
              variant="outlined"
              density="compact"
              hide-details
              class="tfg__grow"
              @update:model-value="setItem(at, { prompt: String($event ?? '') })"
            />
            <SegmentedControl
              :model-value="entry.type"
              :options="typeOptions"
              :label="t('tasks.form.grading.itemType', { n: n + 1 })"
              size="sm"
              @update:model-value="setItem(at, { type: $event as TaskSubmissionEntryType })"
            />
            <BaseButton
              kind="ghost"
              size="sm"
              icon="mdi-close"
              :disabled="group.items.length < 2"
              :title="t('tasks.form.grading.removeItem', { n: n + 1 })"
              :aria-label="t('tasks.form.grading.removeItem', { n: n + 1 })"
              @click="removeItem(at)"
            />
          </div>
          <BaseButton
            kind="secondary"
            size="sm"
            prepend-icon="mdi-plus"
            class="tfg__add"
            @click="addItem(group.stage)"
            >{{ t('tasks.form.grading.addItem') }}</BaseButton
          >
        </div>
      </div>
    </BaseField>

    <BaseField
      data-agent-field="rubric"
      :label="t('tasks.form.grading.rubric')"
      :error="missingName ? t('tasks.form.grading.nameRequired') : undefined"
    >
      <div class="tfg__groups" data-testid="task-form-rubric">
        <div v-for="group in groups" :key="group.key" class="tfg__rubric">
          <p v-if="group.title" class="tfg__stage t-eyebrow-read" data-user-content>{{ group.title }}</p>
          <p v-if="!group.lines.length" class="tfg__empty t-meta-read">{{ t('tasks.form.grading.rubricEmpty') }}</p>
          <template v-else>
            <div v-if="group.manual.length" class="tfg__line tfg__head t-eyebrow-read" aria-hidden="true">
              <span>{{ t('tasks.form.grading.lineItem') }}</span>
              <span>{{ t('tasks.form.grading.linePoints') }}</span>
              <span>{{ t('tasks.form.grading.lineCriteria') }}</span>
              <span />
            </div>
            <div v-for="({ line, at }, n) in group.manual" :key="at" class="tfg__line">
              <v-text-field
                :model-value="line.item"
                :aria-label="t('tasks.form.grading.lineItemOf', { n: n + 1 })"
                :placeholder="t('tasks.form.grading.lineItemPlaceholder')"
                :error="nameMissing(line)"
                autocomplete="off"
                maxlength="64"
                variant="outlined"
                density="compact"
                hide-details
                @update:model-value="setLine(at, { item: String($event ?? '') })"
              />
              <v-text-field
                :model-value="line.points"
                :aria-label="t('tasks.form.grading.linePointsOf', { n: n + 1 })"
                type="number"
                min="0"
                max="1000"
                variant="outlined"
                density="compact"
                hide-details
                class="tfg__points"
                @update:model-value="setLine(at, { points: $event === '' ? 0 : Number($event) })"
              />
              <v-textarea
                :model-value="line.criteria"
                :aria-label="t('tasks.form.grading.lineCriteriaOf', { n: n + 1 })"
                autocomplete="off"
                rows="1"
                auto-grow
                maxlength="2000"
                variant="outlined"
                density="compact"
                hide-details
                @update:model-value="setLine(at, { criteria: String($event ?? '') })"
              />
              <BaseButton
                kind="ghost"
                size="sm"
                icon="mdi-close"
                :title="t('tasks.form.grading.removeLine', { n: n + 1 })"
                :aria-label="t('tasks.form.grading.removeLine', { n: n + 1 })"
                @click="removeLine(at)"
              />
            </div>

            <!-- 额度用量：名称由平台给，分值自己定，给分方式二选一。安静的一个框，不抢手动项的
                 位置；主题色只留给按钮。 -->
            <div v-for="{ line, at } in group.auto" :key="at" class="tfg__auto" data-testid="task-form-usage-line">
              <div class="tfg__line">
                <span class="tfg__usage-name">
                  {{ t('tasks.form.grading.usageName') }}
                  <span class="tfg__tag t-meta-read">{{ t('tasks.form.grading.usageAuto') }}</span>
                </span>
                <v-text-field
                  :model-value="line.points"
                  :aria-label="t('tasks.form.grading.usagePointsOf')"
                  type="number"
                  min="0"
                  max="1000"
                  variant="outlined"
                  density="compact"
                  hide-details
                  class="tfg__points"
                  data-testid="task-form-usage-points"
                  @update:model-value="setLine(at, { points: $event === '' ? 0 : Number($event) })"
                />
                <span class="tfg__usage-rule">{{ ruleText(line) }}</span>
                <BaseButton
                  kind="ghost"
                  size="sm"
                  icon="mdi-close"
                  :title="t('tasks.form.grading.removeUsage')"
                  :aria-label="t('tasks.form.grading.removeUsage')"
                  @click="removeLine(at)"
                />
              </div>
              <div class="tfg__row">
                <span class="tfg__label">{{ t('tasks.form.grading.usageMode') }}</span>
                <SegmentedControl
                  :model-value="usageConfig(line).mode"
                  :options="modeOptions"
                  :label="t('tasks.form.grading.usageMode')"
                  size="sm"
                  data-testid="task-form-usage-mode"
                  @update:model-value="setUsageConfig(at, { mode: $event })"
                />
              </div>

              <template v-if="usageConfig(line).mode === 'threshold'">
                <div class="tfg__cfg">
                  <BaseField :label="t('tasks.form.grading.usageFull')">
                    <v-text-field
                      :model-value="usageConfig(line).full"
                      type="number"
                      min="0"
                      variant="outlined"
                      density="compact"
                      hide-details
                      data-testid="task-form-usage-full"
                      @update:model-value="setUsageConfig(at, { full: $event === '' ? 0 : Number($event) })"
                    />
                  </BaseField>
                  <BaseField :label="t('tasks.form.grading.usageZero')">
                    <v-text-field
                      :model-value="usageConfig(line).zero"
                      type="number"
                      min="0"
                      variant="outlined"
                      density="compact"
                      hide-details
                      data-testid="task-form-usage-zero"
                      @update:model-value="setUsageConfig(at, { zero: $event === '' ? 0 : Number($event) })"
                    />
                  </BaseField>
                </div>
                <p v-if="usageConfig(line).zero <= usageConfig(line).full" class="tfg__warn">
                  {{ t('tasks.form.grading.usageThresholdOrder') }}
                </p>
                <div class="tfg__try">
                  <span class="tfg__label">{{ t('tasks.form.grading.usageTry') }}</span>
                  <span>{{ t('tasks.form.grading.usageTryUsed') }}</span>
                  <v-text-field
                    :model-value="tryCredit(at, usageConfig(line).full, usageConfig(line).zero)"
                    type="number"
                    min="0"
                    variant="outlined"
                    density="compact"
                    hide-details
                    class="tfg__try-input"
                    :aria-label="t('tasks.form.grading.usageTryCredit')"
                    data-testid="task-form-usage-try"
                    @update:model-value="setTryCredit(at, $event === '' ? 0 : Number($event))"
                  />
                  <span>{{ t('tasks.form.grading.usageTryThen') }}</span>
                  <b class="t-num tfg__try-score" data-testid="task-form-usage-try-score">{{
                    usageScore(
                      tryCredit(at, usageConfig(line).full, usageConfig(line).zero),
                      Number(line.points) || 0,
                      usageConfig(line).full,
                      usageConfig(line).zero
                    )
                  }}</b>
                  <span>{{ t('tasks.form.grading.usageTryOutOf', { points: Number(line.points) || 0 }) }}</span>
                </div>
                <p class="tfg__hint t-meta-read">{{ t('tasks.form.grading.usageTryHint') }}</p>
              </template>
              <p v-else class="tfg__hint t-meta-read">
                {{ t('tasks.form.grading.usageRankRule', { points: Number(line.points) || 0 }) }}
              </p>
            </div>
          </template>

          <div class="tfg__foot">
            <v-menu location="bottom start">
              <template #activator="{ props: menu }">
                <BaseButton
                  v-bind="menu"
                  kind="secondary"
                  size="sm"
                  prepend-icon="mdi-plus"
                  append-icon="mdi-menu-down"
                  :aria-label="t('tasks.form.grading.addLine')"
                  data-testid="task-form-add-line"
                  >{{ t('tasks.form.grading.addLine') }}</BaseButton
                >
              </template>
              <v-list density="compact" class="tfg__menu">
                <v-list-item data-testid="task-form-add-manual" @click="addLine(group.stage)">
                  <v-list-item-title>{{ t('tasks.form.grading.addManual') }}</v-list-item-title>
                  <v-list-item-subtitle>{{ t('tasks.form.grading.addManualHint') }}</v-list-item-subtitle>
                </v-list-item>
                <v-list-item data-testid="task-form-add-usage" :disabled="hasUsage" @click="addUsageLine(group.stage)">
                  <v-list-item-title>{{ t('tasks.form.grading.addUsage') }}</v-list-item-title>
                  <v-list-item-subtitle>{{
                    hasUsage ? t('tasks.form.grading.addUsageDone') : t('tasks.form.grading.addUsageHint')
                  }}</v-list-item-subtitle>
                </v-list-item>
              </v-list>
            </v-menu>
            <span v-if="staged && group.lines.length" class="tfg__total t-meta-read">{{
              t('tasks.form.grading.stageTotal', { n: group.total })
            }}</span>
            <span v-else-if="!staged && rubric.length && !usagePoints" class="tfg__total t-meta-read">{{
              t('tasks.form.grading.total', { n: total })
            }}</span>
          </div>
          <!-- 含自动算分那一行的阶段，评分要点合计必须是 100（后端 `rubric._check_automatic`）。 -->
          <p v-if="group.hasAuto && group.total !== AUTO_TOTAL" class="tfg__warn" data-testid="task-form-auto-total">
            {{ t('tasks.form.grading.autoTotal', { required: AUTO_TOTAL, total: group.total }) }}
          </p>
        </div>

        <span
          v-if="rubric.length && (staged || usagePoints)"
          class="tfg__total t-meta-read"
          data-testid="task-form-rubric-total"
        >
          {{
            usagePoints
              ? t('tasks.form.grading.totalUsage', { n: total, usage: usagePoints, percent: usageShare })
              : t('tasks.form.grading.total', { n: total })
          }}
        </span>
      </div>
    </BaseField>

    <v-switch
      v-model="rubricPublic"
      data-agent-field="rubric_public"
      color="primary"
      density="compact"
      hide-details
      inset
      :label="t('tasks.form.grading.public')"
      data-testid="task-form-rubric-public"
    />
  </TaskFormSection>
</template>

<style scoped src="./task-form.css"></style>
<style scoped>
.tfg__groups {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.tfg__items,
.tfg__rubric {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.tfg__stage {
  margin: 0;
  color: var(--muted);
}

.tfg__item {
  display: flex;
  align-items: center;
  gap: 8px;
}

.tfg__n {
  flex: none;
  width: 16px;
  color: var(--muted);
  font-size: 13px;
  text-align: right;
}

.tfg__grow {
  flex: 1 1 auto;
  min-width: 0;
}

.tfg__add {
  align-self: flex-start;
}

.tfg__line {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 88px minmax(0, 2fr) 32px;
  gap: 8px;
  align-items: start;
}

.tfg__head {
  color: var(--muted);
}

.tfg__empty {
  margin: 0;
}

.tfg__foot {
  display: flex;
  align-items: center;
  gap: 8px;
}

.tfg__total {
  margin-left: auto;
}

.tfg__row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

/* 额度用量那一行：安静的一个框；没有琥珀色，自动算分只是一个标记。 */
.tfg__auto {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 12px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
}

.tfg__usage-name {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-top: 6px;
  color: var(--ink);
  font-weight: 500;
}

.tfg__tag {
  padding: 1px 8px;
  border-radius: var(--radius-sm);
  background: var(--fill-2);
  color: var(--ink);
}

.tfg__usage-rule {
  padding-top: 6px;
  color: var(--muted);
  font-size: 12px;
}

.tfg__hint {
  margin: 0;
  color: var(--muted);
  font-size: 12px;
  white-space: pre-line;
}

.tfg__label {
  color: var(--ink);
  font-size: 13px;
  font-weight: 500;
}

.tfg__cfg {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.tfg__try {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.tfg__try-input {
  flex: none;
  width: 96px;
}

.tfg__try-score {
  color: var(--ink);
  font-size: 16px;
}

.tfg__warn {
  margin: 0;
  color: var(--warn-ink);
  font-size: 12px;
}

.tfg__menu :deep(.v-list-item-subtitle) {
  color: var(--muted);
  white-space: normal;
}

@media (max-width: 599px) {
  .tfg__line {
    grid-template-columns: minmax(0, 1fr) 72px 32px;
  }

  .tfg__line > :nth-child(3) {
    grid-column: 1 / span 2;
    grid-row: 2;
  }

  .tfg__head {
    display: none;
  }

  .tfg__cfg {
    grid-template-columns: 1fr;
  }
}
</style>
