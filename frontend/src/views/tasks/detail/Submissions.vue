<template>
  <!-- 「我的提交」页签：我这一份的逐版提交和评审结果。交新一版的按钮在题目名那一行。 -->
  <div class="sm">
    <v-select
      v-if="showIdentitySelect && identityOptions.length > 1"
      v-model="selectedIdentity"
      autocomplete="off"
      :items="identityOptions"
      label="查看身份"
      hide-details
      variant="outlined"
      density="compact"
      class="sm__identity"
    ></v-select>
    <TaskSubmissionHistory
      v-if="taskData && currentParticipantId"
      :task-id="taskData.id"
      :participant-id="currentParticipantId"
      :reviewable="false"
      hide-title
      :highlight-latest="true"
      :outlined="false"
      empty-text="暂无提交记录"
    />
  </div>
</template>

<script setup lang="ts">
import type { TaskParticipationIdentity, TaskParticipationInfo } from '@/network/api/tasks/types'
import type { Task } from '@/types'

import { computed, defineAsyncComponent, onMounted, ref, watch } from 'vue'

import { TasksApi } from '@/network/api/tasks'
import AccountService from '@/services/account'

const TaskSubmissionHistory = defineAsyncComponent(() => import('@/components/tasks/TaskSubmissionHistory.vue'))

const props = defineProps<{
  taskData: Task | null
  isCreator?: boolean
  isAdmin?: boolean
  participationInfo?: TaskParticipationInfo
}>()

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
    let title = identity.type === 'TEAM' ? `队伍: ${identity.teamName || '未命名队伍'}` : '个人参与'

    if (identity.approved !== 'APPROVED') {
      const statusText = identity.approved === 'NONE' ? '(待审核)' : '(已驳回)'
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
</style>
