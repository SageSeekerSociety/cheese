<script setup lang="ts">
// 发题 / 改题那张表单。
//
// 它是**接线**：几节各画各的那一段（`./form/` 下的那几件），值在这一层对上
// `useTaskForm.ts` 的字段（`v-model:<字段>` 给值、`:control` 给 `defineField` 的另一
// 半），提交路上那道实名确认也挂在这里。按钮不在这里：发布 / 保存在页头，页面经
// `defineExpose` 的 `submit` 提交这张表。
//
// 分三块（`TaskFormPart`）：题目内容（说明与材料、阶段、提交与评分）、AI 的设置（默认
// 队友、帮助方式、要求、学习目标、课件、技能，页面塞进来的那一格）、发布设置（参与、
// 时间、分类、更多设置）。AI 的设置是一块，不收在「更多设置」里：它决定从这道题建出的
// 项目里 AI 队友怎么帮人，和题目内容一样要一眼看得见。
//
// 几处由页面塞进来：说明右上角的按钮、附件、AI 的设置，以及一次发好几道时，夹在逐道
// 内容和共用设置之间的那一段（`shared`）。
//
// 它不是场景，按 `frontend_grade.py` 的口径是 A 级：只吃 props、只往上发事件。拆出来的
// 那几件都各自在预览站里有位置（`views/demo/catalogTaskForm.ts`）。
import type { DomainGroup, SpaceCategory, TaskDraftFields, TaskFormSubmitData, TaskStage, Topic } from '@/types'

import { nextTick, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { useTaskForm } from '@/composables/useTaskForm'

import TaskFormClassify from './form/TaskFormClassify.vue'
import TaskFormContent from './form/TaskFormContent.vue'
import TaskFormGrading from './form/TaskFormGrading.vue'
import TaskFormMore from './form/TaskFormMore.vue'
import TaskFormPart from './form/TaskFormPart.vue'
import TaskFormParticipation from './form/TaskFormParticipation.vue'
import TaskFormPrivacyDialog from './form/TaskFormPrivacyDialog.vue'
import TaskFormSection from './form/TaskFormSection.vue'
import TaskFormStages from './form/TaskFormStages.vue'
import TaskFormTime from './form/TaskFormTime.vue'

import { MORE_FIELDS } from '@/lib/taskDraftAgent'

const props = withDefaults(
  defineProps<{
    initialData?: Partial<TaskFormSubmitData> | null
    isEditing?: boolean
    classificationTopics: Topic[]
    categories?: SpaceCategory[]
    selectedCategoryId?: number
    domainGroups?: DomainGroup[]
    /** 一次发好几道：名称和描述逐道在外面填，这张表只管共用的那些。 */
    parametersOnly?: boolean
    /** 能当导师的人：出题人和空间管理员。 */
    mentorOptions?: { title: string; value: string }[]
    /** 新发一道题时导师默认是谁。 */
    defaultMentor?: string
    /** 芝士正在写的那一格（它的字段名）：收起的「更多设置」里的就展开。 */
    agentWriting?: string | null
    /** 改题时已经有人申请或开始做：开始方式不能再改。 */
    startModeLocked?: boolean
    /** 改题页：删掉一个阶段之后能不能存，不能就是服务端那句拒绝（`TaskFormStages`）。 */
    stageRemovalRefusal?: (stages: TaskStage[]) => Promise<string | null>
  }>(),
  {
    initialData: null,
    isEditing: false,
    categories: () => [],
    selectedCategoryId: undefined,
    domainGroups: () => [],
    parametersOnly: false,
    mentorOptions: () => [],
    defaultMentor: undefined,
    agentWriting: null,
    startModeLocked: false,
    stageRemovalRefusal: undefined,
  }
)

const emit = defineEmits<{
  submit: [data: TaskFormSubmitData]
  /** 点过发布之后还拦着的项数，0 = 没有。 */
  invalid: [count: number]
  /** 表单变了（人改的，或者 `apply` 填进来的）：现在的样子，存草稿用。 */
  change: [fields: TaskDraftFields]
  /** 表单挂好了：这是它的起点。 */
  ready: [fields: TaskDraftFields]
}>()

defineSlots<{
  /** 「说明」标题右边的按钮。 */
  'content-actions'?: () => unknown
  /** 描述下面的附件。 */
  attachments?: () => unknown
  /** 一次发好几道时，共用设置前面那一段。 */
  shared?: () => unknown
  /** 「AI 的设置」那一块的字段。 */
  teaching?: () => unknown
}>()

const { t } = useI18n()

// 「题目内容」那一节把编辑器里的正文念出来用一次（提交那一刻 / 弹确认框那一刻）。
const content = ref<{
  readText: () => string | undefined
  readMarkdown: () => string | undefined
} | null>(null)

const {
  topicItems,
  categoryItems,
  domainGroupItems,
  name,
  nameProps,
  submitterType,
  submitterTypeProps,
  rank,
  rankProps,
  registrationStartAt,
  registrationStartAtProps,
  deadline,
  deadlineProps,
  defaultDeadline,
  defaultDeadlineProps,
  startMode,
  topics,
  minTeamSize,
  minTeamSizeProps,
  maxTeamSize,
  maxTeamSizeProps,
  categoryId,
  categoryIdProps,
  requireRealName,
  participantLimit,
  participantLimitProps,
  teamLockingPolicy,
  accessControlEnabled,
  accessDomainGroupIds,
  participantLimitUnlimited,
  mentorsJoin,
  mentors,
  description,
  submissionSchema,
  rubric,
  rubricPublic,
  rubricMissingName,
  stages,
  stageMissingName,
  isSubmitting,
  submitForm,
  check,
  snapshot,
  apply,
  privacyDialogOpen,
  cancelSubmitWithRealName,
  confirmSubmitWithRealName,
} = useTaskForm(props, emit, () => content.value?.readText())

/** 表单现在的样子，连同详情的 Markdown（芝士读写详情用它）。 */
function current(): TaskDraftFields {
  const fields = snapshot()
  const markdown = content.value?.readMarkdown()
  return markdown === undefined ? fields : { ...fields, descriptionMarkdown: markdown }
}

watch(
  () => JSON.stringify(snapshot()),
  () => nextTick(() => emit('change', current()))
)

onMounted(() => nextTick(() => emit('ready', current())))

defineExpose({
  /** 校验并提交；没过就标红，什么都不发。 */
  submit: () => submitForm(),
  /** 只校验、标出缺的格子，不提交。 */
  check: () => check(),
  isSubmitting,
  /** 表单现在的样子。 */
  values: current,
  /** 把这几格填进表单；填完（编辑器也装好了）以后，交回表单现在的样子。 */
  apply: async (fields: TaskDraftFields): Promise<TaskDraftFields> => {
    apply(fields)
    await nextTick()
    return current()
  },
})
</script>

<template>
  <v-form class="task-form" @submit.prevent="submitForm">
    <TaskFormPart :title="t('tasks.form.content')" data-testid="task-form-part-content">
      <TaskFormContent
        v-if="!parametersOnly"
        ref="content"
        v-model:name="name"
        v-model:description="description"
        :name-control="nameProps"
      >
        <template v-if="$slots['content-actions']" #actions><slot name="content-actions" /></template>
        <template v-if="$slots.attachments" #attachments><slot name="attachments" /></template>
      </TaskFormContent>

      <slot name="shared" />

      <TaskFormStages
        v-model:stages="stages"
        v-model:submission-schema="submissionSchema"
        v-model:rubric="rubric"
        :missing-name="stageMissingName"
        :editing="isEditing"
        :removal-refusal="stageRemovalRefusal"
      />

      <TaskFormGrading
        v-model:submission-schema="submissionSchema"
        v-model:rubric="rubric"
        v-model:rubric-public="rubricPublic"
        :stages="stages"
        :missing-name="rubricMissingName"
      />
    </TaskFormPart>

    <TaskFormPart
      v-if="$slots.teaching"
      :title="t('tasks.form.parts.ai')"
      :hint="t('tasks.form.parts.aiHint')"
      data-testid="task-form-part-ai"
    >
      <TaskFormSection>
        <slot name="teaching" />
      </TaskFormSection>
    </TaskFormPart>

    <TaskFormPart :title="t('tasks.form.parts.publish')" data-testid="task-form-part-publish">
      <TaskFormParticipation
        v-model:submitter-type="submitterType"
        v-model:rank="rank"
        v-model:participant-limit="participantLimit"
        v-model:participant-limit-unlimited="participantLimitUnlimited"
        v-model:team-locking-policy="teamLockingPolicy"
        v-model:min-team-size="minTeamSize"
        v-model:max-team-size="maxTeamSize"
        v-model:mentors-join="mentorsJoin"
        v-model:mentors="mentors"
        :mentor-options="mentorOptions"
        :is-editing="isEditing"
        :starts-directly="startMode === 'DIRECT'"
        :submitter-type-control="submitterTypeProps"
        :rank-control="rankProps"
        :participant-limit-control="participantLimitProps"
        :min-team-size-control="minTeamSizeProps"
        :max-team-size-control="maxTeamSizeProps"
      />

      <TaskFormTime
        v-model:registration-start-at="registrationStartAt"
        v-model:deadline="deadline"
        v-model:default-deadline="defaultDeadline"
        v-model:start-mode="startMode"
        :registration-start-at-control="registrationStartAtProps"
        :deadline-control="deadlineProps"
        :default-deadline-control="defaultDeadlineProps"
        :start-mode-locked="startModeLocked"
      />

      <TaskFormClassify
        v-model:category-id="categoryId"
        v-model:topics="topics"
        :category-items="categoryItems"
        :topic-items="topicItems"
        :category-id-control="categoryIdProps"
      />

      <TaskFormMore
        v-model:access-control-enabled="accessControlEnabled"
        v-model:access-domain-group-ids="accessDomainGroupIds"
        v-model:require-real-name="requireRealName"
        :domain-group-items="domainGroupItems"
        :reveal="!!agentWriting && MORE_FIELDS.includes(agentWriting)"
      />
    </TaskFormPart>

    <TaskFormPrivacyDialog
      v-model:open="privacyDialogOpen"
      @confirm="confirmSubmitWithRealName"
      @cancel="cancelSubmitWithRealName"
    />
  </v-form>
</template>
