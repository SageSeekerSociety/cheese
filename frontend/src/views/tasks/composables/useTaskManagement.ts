import type { useTaskData } from './useTaskData'

import { useRouter } from 'vue-router'

import { TaskBoardApi } from '@/api/taskBoard'
import { t } from '@/i18n'
import { toast } from '@/lib/toast'
import { useDialog } from '@/plugins/dialog'
export function useTaskManagement(taskDataModule: ReturnType<typeof useTaskData>) {
  const { taskData } = taskDataModule
  const router = useRouter()
  const dialogs = useDialog()

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
        await TaskBoardApi.del(taskData.value.id)
        toast.success(t('tasks.manage.deleted'))
        router.replace({ name: 'HomeDefault' })
      } catch (error) {
        toast.error(t('tasks.manage.deleteFailed'))
      }
    }
  }

  return {
    confirmDeleteTask,
  }
}
