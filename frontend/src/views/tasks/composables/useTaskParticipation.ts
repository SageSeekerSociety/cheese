import type { useTaskData } from './useTaskData'

import { ref } from 'vue'
import { useRouter } from 'vue-router'

import { useEvents } from '../events'

import { TaskBoardApi } from '@/api/taskBoard'
import { t } from '@/i18n'
import { toast } from '@/lib/toast'
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

  /** 领取、申请、开始做的请求在路上：页头那颗按钮转圈、点不动，确认框的主按钮也一样。 */
  const joining = ref(false)

  /** 一次领取请求：成了返回开出来的项目（申请制的题是 null），没成返回对人说的那句原因。
   *  确认框等它回来才决定关不关：没成就留着，填的字还在，原因写在框里。 */
  const runJoin = async (
    send: () => Promise<{ project: { id: string } | null }>,
    fallback: string
  ): Promise<{ project: { id: string } | null } | string> => {
    if (joining.value) return fallback
    joining.value = true
    events.emit('join-state', { busy: true, error: '' })
    try {
      const result = await send()
      events.emit('join-state', { busy: false, error: '' })
      return result
    } catch (error) {
      // 服务端说了为什么（例如项目正在建、过一会儿再试），就说那一句。
      const reason = await errorHandler.explain(error as Error, fallback)
      events.emit('join-state', { busy: false, error: reason })
      return reason
    } finally {
      joining.value = false
    }
  }

  /** 没发出去就知道不成：原因同样写在确认框里。 */
  const refuse = (reason: string) => {
    events.emit('join-state', { busy: false, error: reason })
    return reason
  }

  // 处理验证信息提交
  const handleVerifyInfoSubmit = async (formData: { phone?: string; email?: string; applyReason?: string }) => {
    if (joining.value) return
    const team = taskData.value?.submitterType === 'TEAM'
    if (team && !selectedTeamId.value) {
      events.emit('verify-dialog-open', false)
      events.emit('team-selection-dialog-open', true)
      return
    }
    const outcome = team
      ? await joinTaskWithTeam(formData, selectedTeamId.value as number)
      : await joinTaskAsIndividual(formData)
    if (typeof outcome === 'string') return
    events.emit('verify-dialog-open', false)
    await loadTaskData()
    if (team) events.emit('reload-joined-teams')
    await opened(outcome.project)
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
    const task = taskData.value
    if (!task) return refuse(t('tasks.participation.submitInfoFailed'))

    return runJoin(
      () => {
        return TaskBoardApi.join(
          task.id,
          {
            email: contactInfo.email,
            phone: contactInfo.phone,
            applyReason: contactInfo.applyReason,
            personalAdvantage: undefined,
            remark: undefined,
            // 直接开始的题，确认框里列出了导师，按下确认就是同意他们加入项目；申请时不问。
            agreeMentors: direct() && (task.mentors?.length ?? 0) > 0,
          },
          teamId
        )
      },
      t(direct() ? 'tasks.participation.startFailed' : 'tasks.participation.teamClaimFailed')
    )
  }

  // 个人参与任务
  const joinTaskAsIndividual = async (contactInfo?: { phone?: string; email?: string; applyReason?: string }) => {
    const task = taskData.value
    if (!task) return refuse(t('tasks.participation.submitInfoFailed'))
    if (!AccountService.user?.id) return refuse(t('tasks.participation.signInFirst'))

    return runJoin(
      () => {
        return TaskBoardApi.join(task.id, {
          email: contactInfo?.email,
          phone: contactInfo?.phone,
          applyReason: contactInfo?.applyReason,
          personalAdvantage: undefined,
          remark: undefined,
          // 直接开始的题，确认框里列出了导师，按下确认就是同意他们加入项目；申请时不问。
          agreeMentors: direct() && (task.mentors?.length ?? 0) > 0,
        })
      },
      t(direct() ? 'tasks.participation.startFailed' : 'tasks.participation.claimFailed')
    )
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
    const task = taskData.value
    if (!task || !claim || joining.value) return
    const outcome = await runJoin(
      () =>
        TaskBoardApi.join(
          task.id,
          { agreeMentors: (task.mentors?.length ?? 0) > 0 },
          claim.type === 'TEAM' ? claim.memberId : undefined
        ),
      t('tasks.participation.startFailed')
    )
    if (typeof outcome === 'string') return
    events.emit('start-dialog-open', false)
    await loadTaskData()
    await opened(outcome.project)
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
    joining,
    onJoinTaskClicked,
    openApprovedClaim,
    handleVerifyInfoSubmit,
    confirmLeaveTask,
    leaveTaskWithTeam,
  }
}
