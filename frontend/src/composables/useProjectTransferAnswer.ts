// Answering an offer to take a project over, from its row in 等你处理 (#3830): the
// call and its toasts live here, the row only clicks.
import { respondToTransfer } from '@/api'
import { t } from '@/i18n'
import { toast } from '@/lib/toast'

export function useProjectTransferAnswer() {
  /** Answers the offer; true when the server took the answer. */
  async function answer(transferId: string, accept: boolean): Promise<boolean> {
    try {
      await respondToTransfer(transferId, accept)
    } catch (error) {
      console.error('Failed to answer the project transfer', error)
      toast.error(
        t(
          accept
            ? 'notifications.PROJECT_TRANSFER.toast.acceptFailed'
            : 'notifications.PROJECT_TRANSFER.toast.declineFailed'
        )
      )
      return false
    }
    toast.success(
      t(accept ? 'notifications.PROJECT_TRANSFER.toast.accepted' : 'notifications.PROJECT_TRANSFER.toast.declined')
    )
    return true
  }

  return { answer }
}
