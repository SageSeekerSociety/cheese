// 频道页上点开一段对话去哪：支线在右半边；频道主线上的一件任务也在右半边审阅（`openTask`），
// 别处点开的任务去任务页，和侧栏任务行点开的是同一处。在频道主线上转为任务，人留在频道里，
// 任务挂在那条消息下面；在支线里转的，接着去那件任务的任务页。
import type { Router } from 'vue-router'
import type { Block, RoomTask } from '@/cx_types'

import { openThread } from '@/api/threads'
import { t } from '@/i18n'
import { queryClient } from '@/query/client'
import { keys } from '@/query/keys'
import { useWorkspaceStore } from '@/stores/workspace'

export function useConversationOpening(opts: {
  router: Router
  place: () => { projectId: string; topicId: string; taskId?: string; threadId?: string; reviewTaskId?: string }
  /** 打开一条支线就算看过它：频道概览里那个点跟着灭。 */
  markSeen: (threadId: string) => void
}) {
  const store = useWorkspaceStore()

  function showThread(threadId: string) {
    const { projectId, topicId } = opts.place()
    opts.markSeen(threadId)
    void opts.router.push({ name: 'workspace-thread', params: { projectId, topicId, threadId } })
  }

  // 主线上一条消息的支线：有就打开，没有就先开一条。已经原地转成任务的，去任务页。
  async function onOpenThread(block: Block) {
    const thread = block.thread
    if (thread?.tasks?.some((task) => task.id === thread.id)) return openTaskPage(thread.id)
    if (thread?.id) return showThread(thread.id)
    try {
      showThread((await openThread(block.id)).id)
    } catch (e) {
      store.reportError(e, t('work.room.thread.openFailed'))
    }
  }

  // 地址里只有任务的编号，它所在的频道由路由的 `addressProps` 补上。
  function taskPage(taskId: string) {
    return { name: 'workspace-task', params: { projectId: opts.place().projectId, taskId } }
  }

  // 任务有自己的页面，浏览器的返回退回原来那一页。
  function openTaskPage(taskId: string) {
    return opts.router.push(taskPage(taskId))
  }

  // 频道主线上点开一件任务（芝士开的那一行、转成的任务、交上来的那一版、「待你审阅」）：
  // 在频道右边审阅它，人还在频道里；要和它的 AI 队友说话，从那里「打开任务」。支线里、
  // 任务页上点开的照旧去任务页。
  function openTask(taskId: string) {
    const { projectId, topicId, taskId: onTask, threadId } = opts.place()
    if (onTask || threadId) return openTaskPage(taskId)
    return opts.router.push({ name: 'workspace-review', params: { projectId, topicId, reviewTaskId: taskId } })
  }

  // 地址上这条支线就是这件任务（原地转成了它）：地址换成任务页，不再留一条支线的记录——
  // 退回到那一条，它又会转到这里。
  function showAsTask(taskId: string) {
    return opts.router.replace(taskPage(taskId))
  }

  async function upgrade(messageId: string): Promise<RoomTask | null> {
    const made = await store.upgradeMessage(messageId)
    // 手上那份支线还写着没转：作废，之后再点开它读回来的是任务。
    if (made) void queryClient.invalidateQueries({ queryKey: keys.roomThread(made.id) })
    return made
  }

  // 支线里转为任务：支线页头那颗是这条支线原地转成任务，支线里的一条回复是另开一件。都接着
  // 去那件任务的任务页，不等侧栏的清单重取回来。
  async function upgradeMessage(messageId: string) {
    const made = await upgrade(messageId)
    if (!made) return
    await (opts.place().threadId === made.id ? showAsTask(made.id) : openTaskPage(made.id))
  }

  // 对话栏里的一条消息转为任务。在频道主线上，人留在频道里：任务挂在那条消息下面，去不去
  // 任务页由人自己点。任务页上的一条消息另开一件，接着去那件的任务页。
  async function upgradeInColumn(messageId: string) {
    if (opts.place().taskId) return upgradeMessage(messageId)
    await upgrade(messageId)
  }

  return { showThread, onOpenThread, openTaskPage, openTask, showAsTask, upgradeMessage, upgradeInColumn }
}
