// 「动态」里那三类行里就能办的邀请 / 申请（项目邀请、团队邀请、加入申请）：请求、
// 两边的话术、答过之后跨行重挂还要记着的状态，全落在这里。以前这三样各自住着一份
// composable，被三个渲染件直接 import；渲染件住在 `components/` 底下，一条 `@/api`
// 都不能碰（`.claude/rules/architecture.md`），那三份于是成了它们藏在身后的取数。
//
// 现在三样同居一处，谁调这一份（`views/InboxView.vue` 那只容器）谁把返回的
// `answering` 桥交下去 —— 渲染件只读桥上递给它的状态、只点桥上的动作，自己不取数。
// 桥的形状见 `components/common/Notification/renders/NotificationRenderUtils.ts`。
import type {
  InvitationAnswer,
  NotificationAnswerBridge,
} from '@/components/common/Notification/renders/NotificationRenderUtils'

import { reactive } from 'vue'
import { toast } from 'vuetify-sonner'

import { respondToInvitation } from '@/api'
import { TeamsApi } from '@/api/teams'
import { t } from '@/i18n'

// 在这儿答过的邀请 / 申请。**记在模块里，不记在行实例里**：答完那一行会换成带链接 /
// 已经处理的那一种，渲染件跟着重建，而列表要到下次重拉才拿到新状态；记在实例里的话，
// 重建之后按钮又回来了，再点只会报「找不到」。三份各按自己的键记：项目邀请按
// invitationId，团队邀请和加入申请按 applicationId。
const projectAnswers = reactive(new Map<string, InvitationAnswer>())
const teamInvitationsAnswered = reactive(new Set<string>())
const joinRequestsAnswered = reactive(new Set<string>())

// 每一行自己的「请求还在路上」：同一个 id 连点、或者点完接受又点拒绝，只发第一下。
// **按 id 分开记，不是一颗全局的 sending** —— 全局那一颗会把不同的邀请相互锁住，两行
// 就没法各答各的了。
const projectInFlight = new Set<string>()
const teamInvitationsInFlight = new Set<string>()
const joinRequestsInFlight = new Set<string>()

export function useNotificationAnswers(): { answering: NotificationAnswerBridge } {
  /** 接受 / 拒绝这条项目邀请；`true` 表示服务端收下了。 */
  async function answerProjectInvitation(invitationId: string, accept: boolean): Promise<boolean> {
    if (!invitationId || projectInFlight.has(invitationId)) return false
    projectInFlight.add(invitationId)
    try {
      await respondToInvitation(invitationId, accept)
    } catch (error) {
      console.error('Failed to answer the project invitation', error)
      toast.error(
        t(
          accept
            ? 'notifications.PROJECT_INVITE.toast.acceptFailed'
            : 'notifications.PROJECT_INVITE.toast.declineFailed'
        )
      )
      return false
    } finally {
      projectInFlight.delete(invitationId)
    }
    projectAnswers.set(invitationId, accept ? 'accepted' : 'declined')
    toast.success(
      t(accept ? 'notifications.PROJECT_INVITE.toast.accepted' : 'notifications.PROJECT_INVITE.toast.declined')
    )
    return true
  }

  /** 接受或拒绝这条团队邀请；`true` 表示服务端收下了。`applicationId` 是实体里那个字符串
   *  id，接口要的是数字。 */
  async function answerTeamInvitation(applicationId: string, accept: boolean): Promise<boolean> {
    if (!applicationId || teamInvitationsInFlight.has(applicationId)) return false
    teamInvitationsInFlight.add(applicationId)
    try {
      await (accept
        ? TeamsApi.acceptInvitation(Number(applicationId))
        : TeamsApi.declineInvitation(Number(applicationId)))
    } catch (error) {
      console.error('Failed to answer team invitation', error)
      toast.error(
        t(
          accept
            ? 'notifications.TEAM_INVITATION.toast.acceptFailed'
            : 'notifications.TEAM_INVITATION.toast.declineFailed'
        )
      )
      return false
    } finally {
      teamInvitationsInFlight.delete(applicationId)
    }
    teamInvitationsAnswered.add(applicationId)
    toast.success(
      t(accept ? 'notifications.TEAM_INVITATION.toast.accepted' : 'notifications.TEAM_INVITATION.toast.declined')
    )
    return true
  }

  /** 批准或拒绝这条申请；`true` 表示服务端收下了。`applicationId` 是实体里那个字符串
   *  id，接口要的是数字。 */
  async function answerJoinRequest(applicationId: string, teamId: number, approve: boolean): Promise<boolean> {
    if (!applicationId || joinRequestsInFlight.has(applicationId)) return false
    joinRequestsInFlight.add(applicationId)
    try {
      await (approve
        ? TeamsApi.approveJoinRequest(teamId, Number(applicationId))
        : TeamsApi.rejectJoinRequest(teamId, Number(applicationId)))
    } catch (error) {
      console.error('Failed to answer team join request', error)
      toast.error(
        t(
          approve
            ? 'notifications.TEAM_JOIN_REQUEST.toast.approveFailed'
            : 'notifications.TEAM_JOIN_REQUEST.toast.rejectFailed'
        )
      )
      return false
    } finally {
      joinRequestsInFlight.delete(applicationId)
    }
    joinRequestsAnswered.add(applicationId)
    toast.success(
      t(approve ? 'notifications.TEAM_JOIN_REQUEST.toast.approved' : 'notifications.TEAM_JOIN_REQUEST.toast.rejected')
    )
    return true
  }

  // 桥上那几处读状态的走普通函数：调用方在 `computed` 里调，读的是上面那几张响应式的
  // 表，所以答过之后那一行自己会跟着重画，不必等列表重拉。
  const answering: NotificationAnswerBridge = {
    projectInvitationAnswer: (invitationId) => projectAnswers.get(invitationId) ?? null,
    answerProjectInvitation,
    teamInvitationAnswered: (applicationId) => teamInvitationsAnswered.has(applicationId),
    answerTeamInvitation,
    joinRequestAnswered: (applicationId) => joinRequestsAnswered.has(applicationId),
    answerJoinRequest,
  }

  return { answering }
}
