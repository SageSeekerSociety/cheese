// Answering a project invitation from wherever it is shown (the bell's render).
// The component shows the invitation and emits; the call and its toasts live here,
// so a component under src/components does not reach the API layer.
import { ref } from 'vue'
import { toast } from 'vuetify-sonner'

import { respondToInvitation } from '@/api'
import { t } from '@/i18n'

export type InvitationAnswer = 'accepted' | 'declined'

export function useProjectInvitationAnswer() {
  /** How it was answered here, before the server's copy of the row says so. */
  const answered = ref<InvitationAnswer | null>(null)

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
    answered.value = accept ? 'accepted' : 'declined'
    toast.success(
      t(accept ? 'notifications.PROJECT_INVITE.toast.accepted' : 'notifications.PROJECT_INVITE.toast.declined')
    )
    return true
  }

  return { answered, answer }
}
