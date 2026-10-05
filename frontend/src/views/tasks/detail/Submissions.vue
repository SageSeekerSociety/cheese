<template>
  <SubmissionsView
    v-model:selected-identity="selectedIdentity"
    :show-identity-select="showIdentitySelect"
    :identity-options="identityOptions"
    :current-participant-id="currentParticipantId"
    :submissions="submissions"
    :has-more="hasMore"
    :loading-more="loadingMore"
    :refreshing="refreshing"
    :total="total"
    @load-more="loadMore"
  />
</template>

<script setup lang="ts">
// 容器：算身份、按身份拉提交记录、翻页都在这儿；画面交给 SubmissionsView。
import type { TaskParticipationInfo } from '@/network/api/tasks/types'
import type { TaskSubmission } from '@/types'
import type { Task } from '@/types'

import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { createEmptyResult, usePaging } from '@/utils/paging'

import SubmissionsView from './SubmissionsView.vue'

import { TasksApi } from '@/network/api/tasks'
import AccountService from '@/services/account'

const { t } = useI18n()

const props = defineProps<{
  taskData: Task | null
  isCreator?: boolean
  isAdmin?: boolean
  participationInfo?: TaskParticipationInfo
}>()

// 状态
const selectedIdentity = ref<number | null>(null)

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

// 逐版提交记录
const {
  data: submissions,
  refresh,
  loadMore,
  hasMore,
  refreshing,
  loadingMore,
  total,
} = usePaging(async (pageStart) => {
  if (!props.taskData || currentParticipantId.value === null) return createEmptyResult<TaskSubmission>()
  const { data } = await TasksApi.listSubmissions(props.taskData.id, currentParticipantId.value, {
    allVersions: true,
    sort_by: 'createdAt',
    sort_order: 'desc',
    pageStart: pageStart,
    pageSize: 10,
    queryReview: true,
  })
  return { data: data.submissions, page: data.page }
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

watch(() => [props.taskData?.id, currentParticipantId.value], refresh)

onMounted(refresh)
</script>
