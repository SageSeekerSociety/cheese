// 房间里一封邮件草稿的两个动作：确认发送、放弃。
//
// 画卡片的那一半（`components/room/MailDraftCard.vue`）只认 props、不碰接口层，
// 所以调接口、记住「刚点完、房间事件还没回来」的那一刻，都在这里。发送仍由后端
// 核对主人身份和附件有没有被改过：这里只是入口，不是闸门。
import type { MailOutcome } from '../lib/platformNotice'

import { ref } from 'vue'

import { discardMailDraft, sendMailDraft } from '../api'

export function useMailDraftActions(draftId: () => string) {
  const busy = ref<'' | 'send' | 'discard'>('')
  const error = ref('')
  /** 刚在这里点完、房间事件还没回来的那一刻，先按自己的结果画。 */
  const local = ref<MailOutcome | null>(null)

  async function act(kind: 'send' | 'discard', fallback: string, run: () => Promise<MailOutcome>) {
    busy.value = kind
    error.value = ''
    try {
      local.value = await run()
    } catch (e) {
      error.value = e instanceof Error ? e.message : fallback
    } finally {
      busy.value = ''
    }
  }

  const send = () =>
    act('send', '没有发出去', async () => {
      const result = await sendMailDraft(draftId())
      return { status: 'sent', sentAt: result.draft.sent_at ?? null, reason: null }
    })

  const discard = () =>
    act('discard', '没有放弃成功', async () => {
      await discardMailDraft(draftId())
      return { status: 'discarded', sentAt: null, reason: null }
    })

  return { busy, error, local, send, discard }
}
