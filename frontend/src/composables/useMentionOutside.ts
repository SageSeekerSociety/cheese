// 我 @ 了不在这个频道里的人：问我要不要把他们加进来，和 Slack 一样。
//
// 谁不在、我能不能加，是后端在回这条消息时一起说的（`mentioned_outside`，只回给发的
// 人，不落库）。这里只记住最近一条的那份，替人点「添加到频道」：走频道名册本来那条
// 加人的路（`addTopicMember`，一次一个人），谁也不会因为被 @ 就自己进来。
//
// 换到别的对话，这一条就不再问：它说的是上一段对话里的那条消息。
import type { MentionedOutside, PostedMessage } from '../api/messages'

import { ref, watch } from 'vue'

import { addTopicMember } from '../api'

import { t } from '@/i18n'
import { toast } from '@/lib/toast'

export function useMentionOutside(options: {
  /** 此刻这一栏读的那段对话；变了就不再问。 */
  place: () => string | null
  /** handle 在界面上叫什么。 */
  nameOf: (handle: string) => string
}) {
  const prompt = ref<MentionedOutside | null>(null)
  const adding = ref(false)

  watch(options.place, () => {
    prompt.value = null
  })

  function names(handles: string[]): string {
    return handles.map(options.nameOf).join(t('work.room.roster.listSeparator'))
  }

  /** 我发的一条落了库（一条消息，或文档存回的一版）：它 @ 到的人里有不在频道的，就问；
   *  没有就不动上一次的。 */
  function offer(block: Pick<PostedMessage, 'mentioned_outside'>) {
    const outside = block.mentioned_outside
    if (outside?.handles.length) prompt.value = outside
  }

  function dismiss() {
    prompt.value = null
  }

  /** 一个一个地加：成了的说一声，没成的留在这一条里，可以再按一次。 */
  async function add() {
    const asked = prompt.value
    if (!asked || !asked.may_add || adding.value) return
    adding.value = true
    const added: string[] = []
    const failed: string[] = []
    let reason = ''
    for (const handle of asked.handles) {
      try {
        await addTopicMember(asked.room_id, handle)
        added.push(handle)
      } catch (error) {
        failed.push(handle)
        reason = error instanceof Error ? error.message : ''
      }
    }
    adding.value = false
    // 加的这一会儿里又发了一条、或者换了对话，那一份不归这里改。
    if (prompt.value === asked) prompt.value = failed.length ? { ...asked, handles: failed } : null
    if (added.length) toast.success(t('work.room.mentionOutside.added', { names: names(added) }))
    if (failed.length) {
      toast.error(
        reason ? t('work.room.mentionOutside.addFailedBecause', { reason }) : t('work.room.mentionOutside.addFailed')
      )
    }
  }

  return { prompt, adding, names, offer, add, dismiss }
}
