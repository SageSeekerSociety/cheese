// 频道页上点开一段对话去哪：支线、支线原地转成的任务在右半边，别的任务去自己的页面；
// 转为任务之后接着显示哪一段。
import type { Router } from 'vue-router'
import type { Block } from '@/cx_types'

import { openThread } from '@/api/threads'
import { t } from '@/i18n'
import { queryClient } from '@/query/client'
import { keys } from '@/query/keys'
import { listedTask } from '@/query/room'
import { useWorkspaceStore } from '@/stores/workspace'

export function useConversationOpening(opts: {
  router: Router
  place: () => { projectId: string; topicId: string; taskId?: string; threadId?: string }
  /** 打开一条支线就算看过它：频道概览里那个点跟着灭。 */
  markSeen: (threadId: string) => void
}) {
  const store = useWorkspaceStore()

  function showThread(threadId: string) {
    const { projectId, topicId } = opts.place()
    opts.markSeen(threadId)
    void opts.router.push({ name: 'workspace-thread', params: { projectId, topicId, threadId } })
  }

  // 主线上一条消息的支线：有就打开，没有就先开一条。
  async function onOpenThread(block: Block) {
    if (block.thread?.id) return showThread(block.thread.id)
    try {
      showThread((await openThread(block.id)).id)
    } catch (e) {
      store.reportError(e, t('work.room.thread.openFailed'))
    }
  }

  // 任务有自己的页面，浏览器的返回退回频道。
  function openTaskPage(taskId: string) {
    const { projectId, topicId } = opts.place()
    void opts.router.push({ name: 'workspace-task', params: { projectId, topicId, taskId } })
  }

  // 支线原地转成的任务在频道页右半边打开，和支线一样；任务页由那里的「整页打开」进。
  function onOpenCard(taskId: string) {
    if (!opts.place().taskId && listedTask(taskId)?.from_thread) return showThread(taskId)
    openTaskPage(taskId)
  }

  // 转为任务：主线上的一条消息（或支线页头那颗），是它的支线原地转成任务，右半边接着
  // 显示这段对话；支线里的一条回复是另开一件，去它的任务页。
  async function upgradeMessage(messageId: string) {
    const made = await store.upgradeMessage(messageId)
    if (!made) return
    if (!made.from_thread) return openTaskPage(made.id)
    await queryClient.invalidateQueries({ queryKey: keys.roomThread(made.id) })
    if (opts.place().threadId !== made.id) showThread(made.id)
  }

  return { showThread, onOpenThread, openTaskPage, onOpenCard, upgradeMessage }
}
