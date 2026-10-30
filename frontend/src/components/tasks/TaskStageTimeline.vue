<script setup lang="ts">
// 阶段时间轴：题目的阶段从左到右排开，每个阶段写名称和截止时间。
//
// 只画，不取数：给了 `statuses`（某一条领取在每个阶段上的状态）就在每个阶段下面
// 写它走到哪一步；给了 `counts`（每个阶段上各状态的人数）就写全体的分布。两样都
// 不给，就只是一条时间轴。`selectable` 时每个阶段是一颗按钮，点了往上报它的序号，
// `current` 那一个标成选中。
import type { TaskStage, TaskStageStatus } from '@/types'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import dayjs from 'dayjs'

import { STAGE_STATUSES, STAGE_TONE } from '@/lib/taskStages'

const props = withDefaults(
  defineProps<{
    stages: TaskStage[]
    statuses?: TaskStageStatus[] | null
    counts?: Partial<Record<TaskStageStatus, number>>[] | null
    selectable?: boolean
    current?: number | null
  }>(),
  { statuses: null, counts: null, selectable: false, current: null }
)

const emit = defineEmits<{ select: [index: number] }>()

const { t } = useI18n()

const steps = computed(() =>
  props.stages.map((stage, index) => {
    const status = props.statuses?.[index] ?? null
    return {
      index,
      name: stage.name,
      due:
        stage.dueAt == null
          ? t('tasks.stages.noDue')
          : t('tasks.stages.due', { when: dayjs(stage.dueAt).format(t('tasks.stages.dateFormat')) }),
      status,
      tone: status ? STAGE_TONE[status] : 'muted',
      counts: props.counts?.[index]
        ? STAGE_STATUSES.map((s) => ({ status: s, n: props.counts![index][s] ?? 0 }))
        : null,
    }
  })
)
</script>

<template>
  <ol class="sst" :aria-label="t('tasks.stages.label')" data-testid="task-stage-timeline">
    <li
      v-for="step in steps"
      :key="step.index"
      class="sst__step"
      :class="[`sst__step--${step.tone}`, { 'sst__step--on': selectable && current === step.index }]"
    >
      <span class="sst__mark" aria-hidden="true" />
      <component
        :is="selectable ? 'button' : 'div'"
        class="sst__body"
        :type="selectable ? 'button' : undefined"
        :aria-pressed="selectable ? current === step.index : undefined"
        @click="selectable && emit('select', step.index)"
      >
        <span class="sst__n t-num">{{ t('tasks.stages.number', { n: step.index + 1 }) }}</span>
        <span class="sst__name" data-user-content>{{ step.name }}</span>
        <span class="sst__due t-meta-read">{{ step.due }}</span>
        <span v-if="step.status" class="sst__status" :class="`sst__status--${step.tone}`">{{
          t(`tasks.stages.status.${step.status}`)
        }}</span>
        <span v-if="step.counts" class="sst__counts t-meta-read">
          <span v-for="c in step.counts" :key="c.status"
            >{{ t(`tasks.stages.status.${c.status}`) }} <span class="t-num">{{ c.n }}</span></span
          >
        </span>
      </component>
    </li>
  </ol>
</template>

<style scoped>
.sst {
  display: flex;
  gap: 0;
  margin: 0;
  padding: 0;
  overflow-x: auto;
  list-style: none;
}

.sst__step {
  position: relative;
  flex: 1 0 160px;
  min-width: 0;
  padding-top: 18px;
}

/* The line between two stages runs through the marks. */
.sst__step::before {
  position: absolute;
  top: 5px;
  right: 0;
  left: 0;
  height: 1px;
  background: var(--line);
  content: '';
}

.sst__step:first-child::before {
  left: 6px;
}

.sst__step:last-child::before {
  right: auto;
  width: 6px;
}

.sst__mark {
  position: absolute;
  top: 0;
  left: 0;
  width: 11px;
  height: 11px;
  border: 2px solid var(--faint);
  border-radius: var(--radius-pill);
  background: var(--surface);
}

.sst__step--warn .sst__mark {
  border-color: var(--warn);
}

.sst__step--ok .sst__mark {
  border-color: var(--ok);
  background: var(--ok);
}

.sst__step--danger .sst__mark {
  border-color: var(--danger);
}

.sst__body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  width: 100%;
  padding: 0 12px 0 0;
  border: 0;
  background: none;
  color: inherit;
  font: inherit;
  text-align: left;
}

button.sst__body {
  cursor: pointer;
}

button.sst__body:hover .sst__name {
  color: var(--ink);
}

button.sst__body:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
  border-radius: var(--radius-sm);
}

.sst__n {
  color: var(--muted);
  font-size: 13px;
}

.sst__name {
  overflow: hidden;
  color: var(--text);
  font-size: 14px;
  font-weight: 600;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.sst__step--on .sst__name {
  color: var(--ink);
  text-decoration: underline;
  text-underline-offset: 4px;
}

.sst__status {
  font-size: 13px;
}

.sst__status--muted {
  color: var(--muted);
}

.sst__status--warn {
  color: var(--warn-ink);
}

.sst__status--ok {
  color: var(--ok-ink);
}

.sst__status--danger {
  color: var(--danger-ink);
}

.sst__counts {
  display: flex;
  flex-wrap: wrap;
  gap: 2px 8px;
}
</style>
