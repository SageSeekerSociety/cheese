import type { PatchTaskRequestData } from '@/network/api/tasks/types'
import type { useTaskData } from './useTaskData'

import { useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'

import { t } from '@/i18n'
import { TasksApi } from '@/network/api/tasks'
import { useDialog } from '@/plugins/dialog'
export function useTaskManagement(taskDataModule: ReturnType<typeof useTaskData>) {
  const { taskData, loadTaskData } = taskDataModule
  const router = useRouter()
  const dialogs = useDialog()

  const submitEditTask = async (updatedTaskData: PatchTaskRequestData) => {
    if (!taskData.value) return

    try {
      await TasksApi.update(taskData.value.id, updatedTaskData)
      toast.success(t('tasks.detail.updateSuccess'))
      await loadTaskData()
    } catch (error) {
      toast.error(t('global.updateFailed'))
      console.error('更新失败', error)
    }
  }

  const confirmDeleteTask = async () => {
    if (!taskData.value) return

    const confirmed = await dialogs
      .confirm(t('tasks.manage.deleteConfirm'), {
        title: t('tasks.manage.deleteConfirmTitle'),
        confirmLabel: t('tasks.page.delete'),
        danger: true,
      })
      .wait()

    if (confirmed) {
      try {
        await TasksApi.del(taskData.value.id)
        toast.success(t('tasks.manage.deleted'))
        router.replace({ name: 'HomeDefault' })
      } catch (error) {
        toast.error(t('tasks.manage.deleteFailed'))
      }
    }
  }

  return {
    submitEditTask,
    confirmDeleteTask,
  }
}
