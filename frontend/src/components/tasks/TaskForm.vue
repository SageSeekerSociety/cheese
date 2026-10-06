<script setup lang="ts">
// 发题 / 改题那张表单。
//
// 它是**接线**：七张卡各画各的那一段（`./form/` 下的那七件），值在这一层对上
// `useTaskForm.ts` 的十六对字段（`v-model:<字段>` 给值、`:control` 给
// `defineField` 的另一半），提交那条路上两道确认弹窗也挂在这里。
//
// 这一层自己不画任何一段表单、也不认识任何一个字段的规矩 —— 想知道「提交之前会拦你
// 哪几条」看 composable，想知道某一张卡长什么样看那一件。
//
// 它不是场景（`docs/manual/dev/scenes.md` 里 scene 的定义：路由到得了的页、
// `components/panels/` 下的一件、或者页的 `<Page>View.vue`），所以 `lint:scenes` 里
// 没有它一格。按 `frontend_grade.py` 的口径它是 A 级：这一层只吃 props、只往上发事件，
// 读 `inject` 那一手在 composable 里，而且是可选的（`PUBLISH_CHECKS_SINK`，取不到就给 null）。
// 拆出来的那几件都各自在预览站里有位置（`views/demo/catalogTaskForm.ts`），整张表自己也
// 在那儿有一格。
import type { DomainGroup, SpaceCategory, TaskFormSubmitData, Topic } from '@/types'

import { ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { useTaskForm } from '@/composables/useTaskForm'

import TaskFormAccessCard from './form/TaskFormAccessCard.vue'
import TaskFormBasicCard from './form/TaskFormBasicCard.vue'
import TaskFormClassifyCard from './form/TaskFormClassifyCard.vue'
import TaskFormDescriptionCard from './form/TaskFormDescriptionCard.vue'
import TaskFormPrivacyDialog from './form/TaskFormPrivacyDialog.vue'
import TaskFormRealNameCard from './form/TaskFormRealNameCard.vue'
import TaskFormTimeCard from './form/TaskFormTimeCard.vue'
import TaskFormVideoCard from './form/TaskFormVideoCard.vue'
import TaskFormVideoDialog from './form/TaskFormVideoDialog.vue'

import BaseButton from '@/components/base/BaseButton.vue'

const { t } = useI18n()

const props = withDefaults(
  defineProps<{
    initialData?: Partial<TaskFormSubmitData> | null
    submitButtonText: string
    isEditing?: boolean
    classificationTopics: Topic[]
    categories?: SpaceCategory[]
    selectedCategoryId?: number
    domainGroups?: DomainGroup[]
    descriptionFormat?: 'markdown' | 'tiptap'
    originalDescription?: string
    parametersOnly?: boolean
  }>(),
  {
    initialData: null,
    submitButtonText: '',
    isEditing: false,
    classificationTopics: () => [],
    categories: () => [],
    selectedCategoryId: undefined,
    domainGroups: () => [],
    descriptionFormat: 'tiptap',
    originalDescription: '',
    parametersOnly: false,
  }
)

const emit = defineEmits<{
  submit: [data: TaskFormSubmitData]
  cancel: []
}>()

const taskForm = ref(null)
// 「题目详情」那张卡把编辑器里的正文念出来用一次（提交那一刻 / 弹确认框那一刻）。
const descriptionCard = ref<{ readText: () => string | undefined } | null>(null)

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
  topicsProps,
  minTeamSize,
  minTeamSizeProps,
  maxTeamSize,
  maxTeamSizeProps,
  categoryId,
  categoryIdProps,
  requireRealName,
  requireRealNameProps,
  participantLimit,
  participantLimitProps,
  teamLockingPolicy,
  teamLockingPolicyProps,
  accessControlEnabled,
  accessControlEnabledProps,
  accessDomainGroupIds,
  accessDomainGroupIdsProps,
  videoUrl,
  videoUrlProps,
  participantLimitUnlimited,
  description,
  markdownDescription,
  isSubmitting,
  submitForm,
  handleCancel,
  privacyDialogOpen,
  cancelSubmitWithRealName,
  confirmSubmitWithRealName,
  videoUrlDialogOpen,
  cancelVideoUrlDialog,
  confirmVideoUrlDialog,
} = useTaskForm(props, emit, () => descriptionCard.value?.readText())
</script>

<template>
  <v-form ref="taskForm" @submit.prevent="submitForm">
    <TaskFormBasicCard
      v-model:name="name"
      v-model:submitter-type="submitterType"
      v-model:rank="rank"
      v-model:participant-limit="participantLimit"
      v-model:participant-limit-unlimited="participantLimitUnlimited"
      v-model:team-locking-policy="teamLockingPolicy"
      v-model:min-team-size="minTeamSize"
      v-model:max-team-size="maxTeamSize"
      :is-editing="isEditing"
      :parameters-only="parametersOnly"
      :name-control="nameProps"
      :submitter-type-control="submitterTypeProps"
      :rank-control="rankProps"
      :participant-limit-control="participantLimitProps"
      :team-locking-policy-control="teamLockingPolicyProps"
      :min-team-size-control="minTeamSizeProps"
      :max-team-size-control="maxTeamSizeProps"
    />

    <TaskFormTimeCard
      v-model:registration-start-at="registrationStartAt"
      v-model:deadline="deadline"
      v-model:default-deadline="defaultDeadline"
      :registration-start-at-control="registrationStartAtProps"
      :deadline-control="deadlineProps"
      :default-deadline-control="defaultDeadlineProps"
    />

    <TaskFormClassifyCard
      v-model:category-id="categoryId"
      v-model:topics="topics"
      :category-items="categoryItems"
      :topic-items="topicItems"
      :category-id-control="categoryIdProps"
      :topics-control="topicsProps"
    />

    <TaskFormRealNameCard
      v-model:require-real-name="requireRealName"
      :require-real-name-control="requireRealNameProps"
    />

    <TaskFormAccessCard
      v-model:access-control-enabled="accessControlEnabled"
      v-model:access-domain-group-ids="accessDomainGroupIds"
      :domain-group-items="domainGroupItems"
      :access-control-enabled-control="accessControlEnabledProps"
      :access-domain-group-ids-control="accessDomainGroupIdsProps"
    />

    <TaskFormDescriptionCard
      ref="descriptionCard"
      v-model:description="description"
      v-model:markdown-description="markdownDescription"
      :description-format="descriptionFormat"
      :parameters-only="parametersOnly"
    />

    <TaskFormVideoCard
      v-model:video-url="videoUrl"
      :video-url-control="videoUrlProps"
      :parameters-only="parametersOnly"
    />

    <div class="d-flex justify-end">
      <slot name="buttons" :is-submitting="isSubmitting">
        <div class="d-flex gap-4">
          <BaseButton v-if="isEditing" kind="ghost" :disabled="isSubmitting" @click="handleCancel">{{
            t('global.cancel')
          }}</BaseButton>
          <BaseButton kind="primary" type="submit" size="lg" :loading="isSubmitting">{{
            submitButtonText || t('tasks.form.submit')
          }}</BaseButton>
        </div>
      </slot>
    </div>

    <!-- 隐私政策确认弹窗 -->
    <TaskFormPrivacyDialog
      v-model:open="privacyDialogOpen"
      @confirm="confirmSubmitWithRealName"
      @cancel="cancelSubmitWithRealName"
    />

    <!-- 视频链接无法解析确认 -->
    <TaskFormVideoDialog
      v-model:open="videoUrlDialogOpen"
      @confirm="confirmVideoUrlDialog"
      @cancel="cancelVideoUrlDialog"
    />
  </v-form>
</template>
