<script setup lang="ts">
// 发题 / 改题那张表单。
//
// 它是**接线**：几节各画各的那一段（`./form/` 下的那几件），值在这一层对上
// `useTaskForm.ts` 的字段（`v-model:<字段>` 给值、`:control` 给 `defineField` 的另一
// 半），提交路上那道实名确认也挂在这里。按钮不在这里：发布 / 保存在页头，页面经
// `defineExpose` 的 `submit` 提交这张表。
//
// 几处由页面塞进来：题目内容右上角的按钮、附件、AI 指导，以及一次发好几道时，夹在逐道
// 内容和共用设置之间的那一段（`shared`）。
//
// 它不是场景，按 `frontend_grade.py` 的口径是 A 级：只吃 props、只往上发事件。拆出来的
// 那几件都各自在预览站里有位置（`views/demo/catalogTaskForm.ts`）。
import type { DomainGroup, SpaceCategory, TaskDraftFields, TaskFormSubmitData, Topic } from '@/types'

import { nextTick, onMounted, ref, watch } from 'vue'

import { useTaskForm } from '@/composables/useTaskForm'

import TaskFormClassify from './form/TaskFormClassify.vue'
import TaskFormContent from './form/TaskFormContent.vue'
import TaskFormGrading from './form/TaskFormGrading.vue'
import TaskFormMore from './form/TaskFormMore.vue'
import TaskFormParticipation from './form/TaskFormParticipation.vue'
import TaskFormPrivacyDialog from './form/TaskFormPrivacyDialog.vue'
import TaskFormTime from './form/TaskFormTime.vue'

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
    /** 这道题有没有单独写 AI 指导（「更多设置」收起时那一行要说）。 */
    teachingCustom?: boolean
    /** 能当导师的人：出题人和空间管理员。 */
    mentorOptions?: { title: string; value: string }[]
    /** 新发一道题时导师默认是谁。 */
    defaultMentor?: string
  }>(),
  {
    initialData: null,
    isEditing: false,
    categories: () => [],
    selectedCategoryId: undefined,
    domainGroups: () => [],
    parametersOnly: false,
    teachingCustom: false,
    mentorOptions: () => [],
    defaultMentor: undefined,
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
  /** 「题目内容」标题右边的按钮。 */
  'content-actions'?: () => unknown
  /** 描述下面的附件。 */
  attachments?: () => unknown
  /** 一次发好几道时，共用设置前面那一段。 */
  shared?: () => unknown
  /** 「更多设置」里的 AI 指导。 */
  teaching?: () => unknown
}>()

// 「题目内容」那一节把编辑器里的正文念出来用一次（提交那一刻 / 弹确认框那一刻）。
const content = ref<{
  readText: () => string | undefined
  readMarkdown: () => string | undefined
  endRect: () => { left: number; top: number; bottom: number } | null
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
  isSubmitting,
  submitForm,
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
  isSubmitting,
  /** 表单现在的样子。 */
  values: current,
  /** 把这几格填进表单；填完（编辑器也装好了）以后，交回表单现在的样子。 */
  apply: async (fields: TaskDraftFields): Promise<TaskDraftFields> => {
    apply(fields)
    await nextTick()
    return current()
  },
  /** 详情末尾在屏幕上的位置。 */
  descriptionEnd: () => content.value?.endRect() ?? null,
})
</script>

<template>
  <v-form class="task-form" @submit.prevent="submitForm">
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

    <TaskFormGrading
      v-model:submission-schema="submissionSchema"
      v-model:rubric="rubric"
      v-model:rubric-public="rubricPublic"
      :missing-name="rubricMissingName"
    />

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
      :registration-start-at-control="registrationStartAtProps"
      :deadline-control="deadlineProps"
      :default-deadline-control="defaultDeadlineProps"
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
      :teaching-custom="teachingCustom"
    >
      <template v-if="$slots.teaching" #teaching><slot name="teaching" /></template>
    </TaskFormMore>

    <TaskFormPrivacyDialog
      v-model:open="privacyDialogOpen"
      @confirm="confirmSubmitWithRealName"
      @cancel="cancelSubmitWithRealName"
    />
  </v-form>
</template>
