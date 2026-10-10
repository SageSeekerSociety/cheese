import type { Task } from '@/types'

import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'

import { truncateString } from '@/utils/form'

import { t } from '@/i18n'
import { TASK_ROUTE_NAMES } from '@/lib/spaceRouteNames'
import { TasksApi } from '@/network/api/tasks'
import { TaskParticipationInfo } from '@/network/api/tasks/types'
import AccountService from '@/services/account'

export function useTaskData() {
  const route = useRoute()
  const routeNames = TASK_ROUTE_NAMES
  const taskId = computed(() => Number(route.params.taskId))

  const taskData = ref<Task | null>(null)
  const loading = ref(true)
  const error = ref<string | null>(null)
  const participationInfo = ref<TaskParticipationInfo>({
    hasParticipation: false,
    identities: [],
  })

  // 计算属性
  const isTaskCreator = computed(() => AccountService.user?.id === taskData.value?.creator.id)
  const isSpaceAdmin = computed(() => {
    if (!taskData.value?.space) return false
    return taskData.value.space.admins.some((admin) => admin.user.id === AccountService.user?.id)
  })

  const titleStartsWithChinesePunctuation = computed(() => {
    const chinesePunctuations = ['【', '《', '「', '『', '（', '〈', '〖', '［', '｛', '〔']
    return chinesePunctuations.some((p) => taskData.value?.name.startsWith(p))
  })

  const breadcrumbItems = computed(() => {
    if (taskData.value?.space) {
      return [
        { title: t('global.cheese'), to: { name: 'HomeDefault' } },
        {
          title: truncateString(taskData.value?.space.name, 12),
          to: { name: routeNames.spaceHome, params: { spaceId: taskData.value?.space.id } },
        },
        {
          title: truncateString(taskData.value?.name, 12),
          to: { name: routeNames.overview, params: { spaceId: taskData.value.space.id, taskId: taskData.value.id } },
        },
      ]
    }
    return null
  })

  const editTaskData = computed(() => {
    if (!taskData.value) return {}
    return {
      name: taskData.value.name,
      submitterType: taskData.value.submitterType,
      rank: taskData.value.rank,
      defaultDeadline: taskData.value.defaultDeadline,
      registrationStartAt: taskData.value.registrationStartAt
        ? new Date(taskData.value.registrationStartAt).getTime()
        : null,
      deadline: taskData.value.deadline,
      resubmittable: taskData.value.resubmittable,
      editable: taskData.value.editable,
      // 编辑器的 JSON，或者从 PDF 导入时存下的 Markdown：表单两种都认（`descriptionDoc`）。
      description: taskData.value.description,
      requireRealName: taskData.value.requireRealName,
      minTeamSize: taskData.value.minTeamSize,
      maxTeamSize: taskData.value.maxTeamSize,
      participantLimit: taskData.value.participantLimit,
      teamLockingPolicy: taskData.value.teamLockingPolicy,
      categoryId: taskData.value.category?.id,
      accessControlEnabled: taskData.value.accessControlEnabled,
      accessDomainGroupIds: taskData.value.accessDomainGroupIds ?? [],
    }
  })

  // 加载任务数据。`quiet` 是页面已经画着、只是这道题上的事变了：不转圈，读不到也不把整页换成报错。
  const loadTaskData = async ({ quiet = false }: { quiet?: boolean } = {}) => {
    if (!quiet) {
      loading.value = true
      error.value = null
    }

    try {
      const { data } = await TasksApi.detail(taskId.value)
      taskData.value = data.task

      // 获取参与身份信息
      if (data.participation) {
        participationInfo.value = data.participation
      } else {
        participationInfo.value = {
          hasParticipation: false,
          identities: [],
        }
      }
    } catch (err) {
      if (!quiet) error.value = err instanceof Error ? err.message : t('tasks.loadFailed')
      console.error('Failed to load task:', err)
    } finally {
      loading.value = false
    }
  }

  return {
    taskId,
    taskData,
    loading,
    error,
    isTaskCreator,
    isSpaceAdmin,
    titleStartsWithChinesePunctuation,
    breadcrumbItems,
    editTaskData,
    loadTaskData,
    participationInfo,
  }
}
