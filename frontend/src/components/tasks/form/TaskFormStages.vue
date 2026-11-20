<script setup lang="ts">
// 「阶段」那一节：把题目分成几个阶段，每个阶段有名称、要做到什么和截止时间，可以
// 增删、调整先后。不分阶段时这一节只有一句「不分阶段」和一颗添加按钮。
//
// 它只画：三样值都是 props 进来的。阶段、提交项、评分要点按序号连着（提交项和评分
// 要点的 `stage` 指它属于哪个阶段），所以加、删、挪一个阶段时三样一起往上报，怎么跟着
// 变由 `lib/taskStages.ts` 说。
import type { StagedForm } from '@/lib/taskStages'
import type { TaskRubricLine, TaskStage, TaskSubmissionSchemaEntry } from '@/types'

import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { VDateInput } from 'vuetify/labs/VDateInput'

import TaskFormSection from './TaskFormSection.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import ConfirmDialog from '@/components/base/ConfirmDialog.vue'
import { addStage, moveStage, removeStage } from '@/lib/taskStages'

const props = defineProps<{
  /** 点过发布后，有没写名称的阶段。 */
  missingName?: boolean
  /** 改题页：删掉的阶段要等「保存更改」才落到题目上。 */
  editing?: boolean
  /** 改题页：删掉一个阶段之后的样子能不能存，不能就交回服务端那句拒绝（有人交过的阶段
   *  删不掉）。点 ✕ 时就问，不等到保存。 */
  removalRefusal?: (stages: TaskStage[]) => Promise<string | null>
}>()

const stages = defineModel<TaskStage[]>('stages', { required: true })
const items = defineModel<TaskSubmissionSchemaEntry[]>('submissionSchema', { required: true })
const rubric = defineModel<TaskRubricLine[]>('rubric', { required: true })

const { t } = useI18n()

function write(next: StagedForm) {
  stages.value = next.stages
  items.value = next.items
  rubric.value = next.rubric
}

const form = (): StagedForm => ({ stages: stages.value, items: items.value, rubric: rubric.value })

function setStage(index: number, patch: Partial<TaskStage>) {
  stages.value = stages.value.map((stage, i) => (i === index ? { ...stage, ...patch } : stage))
}

/** 截止时间选的是哪一天，存的是那一天的最后一刻（和题目的截止一样）。 */
function setDue(index: number, value: unknown) {
  if (!(value instanceof Date)) return setStage(index, { dueAt: null })
  const due = new Date(value)
  due.setHours(23, 59, 59, 999)
  setStage(index, { dueAt: due.getTime() })
}

const nameMissing = (stage: TaskStage) => props.missingName && !stage.name.trim()

/** 要删的那一个阶段的序号：先问一句，答「删除」才删。 */
const removing = ref<number | null>(null)
const confirmingRemove = ref(false)

/** 点 ✕ 时服务端说的不能删的原因。 */
const refusal = ref('')

async function askRemove(index: number) {
  refusal.value = ''
  const reason = props.removalRefusal ? await props.removalRefusal(stages.value.filter((_, i) => i !== index)) : null
  if (reason) {
    refusal.value = reason
    return
  }
  removing.value = index
  confirmingRemove.value = true
}

function confirmRemove() {
  const index = removing.value
  confirmingRemove.value = false
  removing.value = null
  if (index !== null) write(removeStage(form(), index))
}
</script>

<template>
  <TaskFormSection :title="t('tasks.form.stages.title')">
    <div class="tfst" data-agent-field="stages" data-testid="task-form-stages">
      <p v-if="!stages.length" class="tfst__empty t-meta-read">{{ t('tasks.form.stages.none') }}</p>
      <div v-for="(stage, index) in stages" :key="index" class="tfst__stage">
        <div class="tfst__row">
          <span class="tfst__n t-num">{{ index + 1 }}</span>
          <v-text-field
            :model-value="stage.name"
            :aria-label="t('tasks.form.stages.nameOf', { n: index + 1 })"
            :placeholder="t('tasks.form.stages.namePlaceholder')"
            :error="nameMissing(stage)"
            autocomplete="off"
            maxlength="64"
            variant="outlined"
            density="compact"
            hide-details
            class="tfst__grow"
            @update:model-value="setStage(index, { name: String($event ?? '') })"
          />
          <VDateInput
            :model-value="stage.dueAt ? new Date(stage.dueAt) : null"
            :aria-label="t('tasks.form.stages.dueOf', { n: index + 1 })"
            :placeholder="t('tasks.form.stages.due')"
            prepend-icon=""
            variant="outlined"
            density="compact"
            clearable
            hide-details
            class="tfst__due"
            @update:model-value="setDue(index, $event)"
          />
          <BaseButton
            kind="ghost"
            size="sm"
            icon="mdi-arrow-up"
            :disabled="index === 0"
            :title="t('tasks.form.stages.moveUp', { n: index + 1 })"
            :aria-label="t('tasks.form.stages.moveUp', { n: index + 1 })"
            @click="write(moveStage(form(), index, index - 1))"
          />
          <BaseButton
            kind="ghost"
            size="sm"
            icon="mdi-arrow-down"
            :disabled="index === stages.length - 1"
            :title="t('tasks.form.stages.moveDown', { n: index + 1 })"
            :aria-label="t('tasks.form.stages.moveDown', { n: index + 1 })"
            @click="write(moveStage(form(), index, index + 1))"
          />
          <BaseButton
            kind="ghost"
            size="sm"
            icon="mdi-close"
            :title="t('tasks.form.stages.remove', { n: index + 1 })"
            :aria-label="t('tasks.form.stages.remove', { n: index + 1 })"
            @click="askRemove(index)"
          />
        </div>
        <v-textarea
          :model-value="stage.description"
          :aria-label="t('tasks.form.stages.descriptionOf', { n: index + 1 })"
          :placeholder="t('tasks.form.stages.descriptionPlaceholder')"
          autocomplete="off"
          rows="1"
          auto-grow
          maxlength="2000"
          variant="outlined"
          density="compact"
          hide-details
          class="tfst__desc"
          @update:model-value="setStage(index, { description: String($event ?? '') })"
        />
      </div>
      <p v-if="missingName" class="tfst__error" role="alert">{{ t('tasks.form.stages.nameRequired') }}</p>
      <p v-if="refusal" class="tfst__error" role="alert" data-testid="stage-remove-refused">{{ refusal }}</p>
      <BaseButton
        kind="secondary"
        size="sm"
        prepend-icon="mdi-plus"
        class="tfst__add"
        @click="write(addStage(form()))"
        >{{ t('tasks.form.stages.add') }}</BaseButton
      >
    </div>
    <ConfirmDialog
      v-model="confirmingRemove"
      :title="t('tasks.form.stages.removeTitle', { n: (removing ?? 0) + 1 })"
      :confirm-label="t('tasks.form.stages.removeConfirm')"
      danger
      @confirm="confirmRemove"
    >
      <template v-if="editing" #default>{{ t('tasks.form.stages.removeBody') }}</template>
    </ConfirmDialog>
  </TaskFormSection>
</template>

<style scoped src="./task-form.css"></style>
<style scoped>
.tfst {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.tfst__empty {
  margin: 0;
}

.tfst__stage {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.tfst__row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.tfst__n {
  flex: none;
  width: 16px;
  color: var(--muted);
  font-size: 13px;
  text-align: right;
}

.tfst__grow {
  flex: 1 1 auto;
  min-width: 0;
}

.tfst__due {
  flex: 0 0 168px;
}

.tfst__desc {
  margin-left: 24px;
}

.tfst__error {
  margin: 0;
  color: var(--danger-ink);
  font-size: 13px;
}

.tfst__add {
  align-self: flex-start;
}

@media (max-width: 599px) {
  .tfst__row {
    flex-wrap: wrap;
  }

  .tfst__due {
    flex: 1 1 100%;
    order: 1;
    margin-left: 24px;
  }
}
</style>
