import type { useTaskData } from './useTaskData'

import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'

import { useEvents } from '../events'

import { TaskBoardApi } from '@/api/taskBoard'
import { t } from '@/i18n'
import { useDialog } from '@/plugins/dialog'
import AccountService from '@/services/account'
import errorHandler from '@/services/ErrorHandler'

export function useTaskParticipation(taskDataModule: ReturnType<typeof useTaskData>) {
  const { taskData, loadTaskData } = taskDataModule
  const dialogs = useDialog()
  const events = useEvents()
  const router = useRouter()

  const selectedTeamId = ref<number | null>(null)

  /** 直接开始的题点了就开始；申请制的题先申请，等出题人通过。 */
  const direct = () => taskData.value?.startMode === 'DIRECT'

  /** 开始了就进项目；申请制的题申请时还没有项目（通过后点「开始做这道题」才建），留在题目页。 */
  const opened = async (project: { id: string } | null) => {
    if (project) {
      toast.success(t('tasks.participation.started'))
      await router.push(`/projects/${project.id}`)
    } else {
      toast.success(t('tasks.participation.applied'))
    }
  }

  // 注册事件监听
  events.on('select-team', (teamId: number) => {
    selectedTeamId.value = teamId
    events.emit('verify-dialog-open', true)
  })

  // 处理参与任务逻辑
  const onJoinTaskClicked = () => {
    if (taskData.value?.submitterType === 'TEAM') {
      events.emit('team-selection-dialog-open', true)
    } else {
      events.emit('verify-dialog-open', true)
    }
  }

  // 处理验证信息提交
  const handleVerifyInfoSubmit = async (formData: { phone?: string; email?: string; applyReason?: string }) => {
    try {
      events.emit('verify-dialog-open', false)

      if (taskData.value?.submitterType === 'TEAM') {
        if (!selectedTeamId.value) {
          events.emit('team-selection-dialog-open', true)
          return
        }
        await joinTaskWithTeam(formData, selectedTeamId.value)
      } else {
        await joinTaskAsIndividual(formData)
      }
    } catch (error) {
      toast.error(t('tasks.participation.submitInfoFailed'))
    }
  }

  // 团队参与任务
  const joinTaskWithTeam = async (
    contactInfo: {
      phone?: string
      email?: string
      applyReason?: string
    },
    teamId: number
  ) => {
    if (!taskData.value || !teamId) return

    try {
      const result = await TaskBoardApi.join(
        taskData.value.id,
        {
          email: contactInfo.email,
          phone: contactInfo.phone,
          applyReason: contactInfo.applyReason,
          personalAdvantage: undefined,
          remark: undefined,
          // 直接开始的题，确认框里列出了导师，按下确认就是同意他们加入项目；申请时不问。
          agreeMentors: direct() && (taskData.value.mentors?.length ?? 0) > 0,
        },
        teamId
      )

      await loadTaskData()
      events.emit('reload-joined-teams')
      await opened(result.project)
    } catch (error) {
      // 服务端说了为什么（例如项目正在建、过一会儿再试），就说那一句。
      void errorHandler.handle(error as Error, {
        defaultMessage: t(direct() ? 'tasks.participation.startFailed' : 'tasks.participation.teamClaimFailed'),
      })
    }
  }

  // 个人参与任务
  const joinTaskAsIndividual = async (contactInfo?: { phone?: string; email?: string; applyReason?: string }) => {
    if (!taskData.value) return
    try {
      const userId = AccountService.user?.id
      if (!userId) {
        toast.error(t('tasks.participation.signInFirst'))
        return
      }

      const result = await TaskBoardApi.join(taskData.value.id, {
        email: contactInfo?.email,
        phone: contactInfo?.phone,
        applyReason: contactInfo?.applyReason,
        personalAdvantage: undefined,
        remark: undefined,
        // 直接开始的题，确认框里列出了导师，按下确认就是同意他们加入项目；申请时不问。
        agreeMentors: direct() && (taskData.value.mentors?.length ?? 0) > 0,
      })

      await loadTaskData()
      await opened(result.project)
    } catch (error) {
      void errorHandler.handle(error as Error, {
        defaultMessage: t(direct() ? 'tasks.participation.startFailed' : 'tasks.participation.claimFailed'),
      })
    }
  }

  /** 已经通过、还没有项目的那份领取（申请制的题通过了，或上次建仓库没成）：点「开始做这道题」
   *  先弹开始的确认框（导师在这一刻问），按了「开始做」才用这道题新建项目。联系方式申请时
   *  已经给过，不再问。 */
  const startingClaim = ref<{ type: 'USER' | 'TEAM'; memberId: number } | null>(null)
  const openApprovedClaim = (claim: { type: 'USER' | 'TEAM'; memberId: number }) => {
    startingClaim.value = claim
    events.emit('start-dialog-open', true)
  }

  events.on('start-confirmed', async () => {
    const claim = startingClaim.value
    if (!taskData.value || !claim) return
    try {
      const result = await TaskBoardApi.join(
        taskData.value.id,
        { agreeMentors: (taskData.value.mentors?.length ?? 0) > 0 },
        claim.type === 'TEAM' ? claim.memberId : undefined
      )
      await loadTaskData()
      await opened(result.project)
    } catch (error) {
      void errorHandler.handle(error as Error, { defaultMessage: t('tasks.participation.startFailed') })
    }
  })

  // 退出任务主逻辑
  const confirmLeaveTask = async () => {
    if (!taskData.value) return

    if (taskData.value.submitterType === 'TEAM') {
      const joinedTeams = taskData.value.joinedTeams || []

      if (joinedTeams.length > 1) {
        events.emit('leave-team-dialog-open', true)
      } else if (joinedTeams.length === 1) {
        const teamId = joinedTeams[0].id
        const confirmed = await dialogs
          .confirm(t('tasks.participation.leaveTeamConfirm', { name: joinedTeams[0].name }), {
            title: t('tasks.participation.leaveConfirmTitle'),
          })
          .wait()

        if (confirmed) {
          await leaveTaskWithTeam(teamId)
        }
      } else {
        toast.info(t('tasks.participation.noTeamJoined'))
      }
    } else {
      const confirmed = await dialogs
        .confirm(t('tasks.participation.leaveConfirm'), {
          title: t('tasks.participation.leaveConfirmTitle'),
        })
        .wait()

      if (confirmed) {
        await leaveTaskAsIndividual()
      }
    }
  }

  // 个人退出任务
  const leaveTaskAsIndividual = async () => {
    if (!taskData.value) return

    const userId = AccountService.user?.id
    if (!userId) return

    try {
      await TaskBoardApi.removeParticipantByMemberId(taskData.value.id, userId)
      toast.success(t('tasks.participation.left'))
      await loadTaskData()
    } catch (error) {
      toast.error(t('tasks.participation.leaveFailed'))
    }
  }

  // 团队退出任务
  const leaveTaskWithTeam = async (teamId: number) => {
    if (!taskData.value || !teamId) return

    try {
      await TaskBoardApi.removeParticipantByMemberId(taskData.value.id, teamId)
      toast.success(t('tasks.participation.teamLeft'))
      await loadTaskData()
    } catch (error) {
      toast.error(t('tasks.participation.leaveFailed'))
      console.error('Failed to leave task:', error)
    }
  }

  return {
    onJoinTaskClicked,
    openApprovedClaim,
    handleVerifyInfoSubmit,
    confirmLeaveTask,
    leaveTaskWithTeam,
  }
}
