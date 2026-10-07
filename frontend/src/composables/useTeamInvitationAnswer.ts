// 「接受 / 拒绝这条团队邀请」：请求和两边的话术都落在这里；
// `components/common/Notification/renders/RenderTeamInvitationNotification.vue` 只画
// 收到的状态、点这里给的回调——组件不吃 API 层（.claude/rules/architecture.md）。
import { reactive, ref } from 'vue'
import { toast } from 'vuetify-sonner'

import { t } from '@/i18n'
import { TeamsApi } from '@/network/api/teams'

// 在这儿答过的邀请。**记在模块里，不记在组件实例里**：答完这一行会换成带链接的那一种，
// 渲染器跟着重建，而列表要到下次重拉才拿到新状态；记在实例里的话，重建之后按钮又回来了，
// 再点只会报「找不到」。
const answeredHere = reactive(new Set<string>())

export function useTeamInvitationAnswer() {
  /** 请求还没回来时再点不算数：连点、或者点完接受又点拒绝，只发第一下。 */
  const sending = ref(false)

  /** 接受或拒绝这条邀请；true 表示服务端收下了。`applicationId` 是实体里那个字符串 id，
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
    answeredHere.add(applicationId)
    toast.success(
      t(accept ? 'notifications.TEAM_INVITATION.toast.accepted' : 'notifications.TEAM_INVITATION.toast.declined')
    )
    return true
  }

  return { answeredHere, answer }
}
