// 频道页上点开一段对话去哪：支线在右半边；任务（支线原地转成的、另开的、新建的）去任务页，
// 和侧栏任务行点开的是同一处。转为任务之后接着去那件任务的任务页。
import type { Router } from 'vue-router'
import type { Block } from '@/cx_types'

import { openThread } from '@/api/threads'
import { t } from '@/i18n'
import { queryClient } from '@/query/client'
import { keys } from '@/query/keys'
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

  // 地址上这条支线就是这件任务（原地转成了它）：地址换成任务页，不再留一条支线的记录——
  // 退回到那一条，它又会转到这里。
  function showAsTask(taskId: string) {
    return opts.router.replace(taskPage(taskId))
  }

  // 转为任务：主线上的一条消息（或支线页头那颗），是它的支线原地转成任务；支线里的一条
  // 回复是另开一件。都接着去那件任务的任务页，不等侧栏的清单重取回来。
  async function upgradeMessage(messageId: string) {
    const made = await store.upgradeMessage(messageId)
    if (!made) return
    // 手上那份支线还写着没转：作废，之后再点开它读回来的是任务。
    void queryClient.invalidateQueries({ queryKey: keys.roomThread(made.id) })
    await (opts.place().threadId === made.id ? showAsTask(made.id) : openTaskPage(made.id))
  }

  return { showThread, onOpenThread, openTaskPage, showAsTask, upgradeMessage }
}
