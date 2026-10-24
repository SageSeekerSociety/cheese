<template>
  <!-- 「我的提交」页签：我这一份的逐版提交和评审结果。交新一版的按钮在题目名那一行。 -->
  <div class="sm">
    <v-select
      v-if="showIdentitySelect && identityOptions.length > 1"
      v-model="selectedIdentity"
      autocomplete="off"
      :items="identityOptions"
      :label="t('tasks.submissions.identity')"
      hide-details
      variant="outlined"
      density="compact"
      class="sm__identity"
    ></v-select>
    <!-- A staged task is handed in and graded stage by stage: pick the stage to read. -->
    <TaskStageTimeline
      v-if="stages.length"
      class="sm__stages"
      :stages="stages"
      :statuses="progress?.mine?.stages"
      selectable
      :current="stage"
      @select="stage = $event"
    />
    <TaskSubmissionHistory
      v-if="taskData && currentParticipantId"
      :task-id="taskData.id"
      :participant-id="currentParticipantId"
      :stage="stages[stage]?.id ?? null"
      :reviewable="false"
      hide-title
      :highlight-latest="true"
      :outlined="false"
      :empty-text="t('tasks.submissions.empty')"
    />
  </div>
</template>

<script setup lang="ts">
import type { Task, TaskProgress } from '@/types'
import type { TaskParticipationIdentity, TaskParticipationInfo } from '@/types/taskBoard'

import { computed, defineAsyncComponent, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import TaskStageTimeline from '@/components/tasks/TaskStageTimeline.vue'
import AccountService from '@/services/account'

const { t } = useI18n()

const TaskSubmissionHistory = defineAsyncComponent(() => import('@/components/tasks/TaskSubmissionHistory.vue'))

const props = defineProps<{
  taskData: Task | null
  isCreator?: boolean
  isAdmin?: boolean
  participationInfo?: TaskParticipationInfo
  /** 每个阶段上的进度，题目页取的那一份。 */
  progress?: TaskProgress | null
}>()

const stages = computed(() => props.taskData?.stages ?? [])
/** 看哪个阶段的提交：默认第一个还没通过的。 */
const stage = ref(0)
watch(
  () => props.progress?.mine?.stages,
  (mine) => {
    const open = mine?.findIndex((s) => s !== 'PASSED') ?? -1
    stage.value = open >= 0 ? open : 0
  },
  { immediate: true }
)

// 状态
const selectedIdentity = ref<number | null>(null)
const participants = ref<any[]>([])

// 计算属性
const isCreator = computed(() => props.isCreator || AccountService.user?.id === props.taskData?.creator.id)
const isAdmin = computed(
  () =>
    props.isAdmin || (props.taskData?.space?.admins || []).some((admin) => admin.user.id === AccountService.user?.id)
)

// 获取可供选择的身份选项
const identityOptions = computed(() => {
  if (!props.participationInfo?.identities.length) return []

  // 将所有身份转换为选择项
  return props.participationInfo.identities.map((identity) => {
    let title =
      identity.type === 'TEAM'
        ? t('tasks.submissions.teamIdentity', { name: identity.teamName || t('tasks.submissions.unnamedTeam') })
        : t('tasks.submissions.individualIdentity')

    if (identity.approved !== 'APPROVED') {
      const statusText = identity.approved === 'NONE' ? t('tasks.submissions.pending') : t('tasks.submissions.rejected')
      title = `${title} ${statusText}`
    }

    return {
      title,
      value: identity.id,
      disabled: identity.approved !== 'APPROVED',
    }
  })
})

// 是否显示身份选择器
const showIdentitySelect = computed(() => {
  return props.participationInfo?.hasParticipation && props.participationInfo.identities.length > 0
})

// 计算当前查看的参与者ID
const currentParticipantId = computed(() => {
  // 如果用户选择了特定身份
  if (selectedIdentity.value) {
    return selectedIdentity.value
  }

  // 如果没有选择但有身份，使用第一个可提交的身份
  if (props.participationInfo?.identities.length) {
    const approvedIdentity = props.participationInfo.identities.find((i) => i.approved === 'APPROVED')
    if (approvedIdentity) {
      return approvedIdentity.id
    }
    // 如果没有已批准的身份，返回第一个身份
    return props.participationInfo.identities[0].id
  }

  // 如果是管理员或创建者，可以查看其他人
  if ((isAdmin.value || isCreator.value) && selectedIdentity.value) {
    return selectedIdentity.value
  }

  return null
})

// 监听和生命周期
watch(
  () => props.participationInfo,
  (newVal) => {
    if (newVal?.identities.length) {
      // 默认选择第一个已批准的身份
      const approvedIdentity = newVal.identities.find((i) => i.approved === 'APPROVED')
      if (approvedIdentity) {
        selectedIdentity.value = approvedIdentity.id
      } else {
        selectedIdentity.value = newVal.identities[0].id
      }
    }
  },
  { immediate: true }
)

onMounted(() => {
  if (props.participationInfo?.identities.length) {
    // 默认选择第一个已批准的身份
    const approvedIdentity = props.participationInfo.identities.find((i) => i.approved === 'APPROVED')
    if (approvedIdentity) {
      selectedIdentity.value = approvedIdentity.id
    } else {
      selectedIdentity.value = props.participationInfo.identities[0].id
    }
  }
})
</script>

<style scoped>
.sm__identity {
  max-width: 240px;
  margin-bottom: 16px;
}

.sm__stages {
  margin-bottom: 16px;
}
</style>
