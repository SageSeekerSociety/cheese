// Answering a project invitation from its row in 等你处理: the call and its toasts
// live here, the row only clicks.
import { respondToInvitation } from '@/api'
import { t } from '@/i18n'
import { toast } from '@/lib/toast'

export function useProjectInvitationAnswer() {
  /** Answers the invitation; true when the server took the answer. */
  async function answer(invitationId: string, accept: boolean): Promise<boolean> {
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
    }
    toast.success(
      t(accept ? 'notifications.PROJECT_INVITE.toast.accepted' : 'notifications.PROJECT_INVITE.toast.declined')
    )
    return true
  }

  return { answer }
}
