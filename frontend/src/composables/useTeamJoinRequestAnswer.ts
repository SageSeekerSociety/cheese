// 「批准 / 拒绝这条加入申请」：请求和两边的话术都落在这里，待办页「等你处理」那一行
// 只管点。
import { ref } from 'vue'

import { TeamsApi } from '@/api/teams'
import { t } from '@/i18n'
import { toast } from '@/lib/toast'

export function useTeamJoinRequestAnswer() {
  /** 请求还没回来时再点不算数：连点、或者点完批准又点拒绝，只发第一下。 */
  const sending = ref(false)

  /** 批准或拒绝这条申请；true 表示服务端收下了。`applicationId` 是清单里那个字符串 id，
   *  接口要的是数字。 */
  async function answer(applicationId: string, teamId: number, approve: boolean): Promise<boolean> {
    if (sending.value) return false
    sending.value = true
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
      sending.value = false
    }
    toast.success(
      t(approve ? 'notifications.TEAM_JOIN_REQUEST.toast.approved' : 'notifications.TEAM_JOIN_REQUEST.toast.rejected')
    )
    return true
  }

  return { answer }
}
