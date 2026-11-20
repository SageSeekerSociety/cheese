// 「接受 / 拒绝这条团队邀请」：请求和两边的话术都落在这里，待办页「等你处理」那一行
// 只管点。
import { ref } from 'vue'

import { TeamsApi } from '@/api/teams'
import { t } from '@/i18n'
import { toast } from '@/lib/toast'

export function useTeamInvitationAnswer() {
  /** 请求还没回来时再点不算数：连点、或者点完接受又点拒绝，只发第一下。 */
  const sending = ref(false)

  /** 接受或拒绝这条邀请；true 表示服务端收下了。`applicationId` 是清单里那个字符串 id，
   *  接口要的是数字。 */
  async function answer(applicationId: string, accept: boolean): Promise<boolean> {
    if (sending.value) return false
    sending.value = true
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
      sending.value = false
    }
    toast.success(
      t(accept ? 'notifications.TEAM_INVITATION.toast.accepted' : 'notifications.TEAM_INVITATION.toast.declined')
    )
    return true
  }

  return { answer }
}
