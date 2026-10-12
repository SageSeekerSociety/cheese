// 任务页顶上那句话在哪：频道主线、支线，或另一个任务。点它就去那段对话、停在这一条上
// （`?block=`，对话栏和支线那一栏都认它）。
import type { RouteLocationRaw } from 'vue-router'
import type { TaskSource } from '@/types/taskOrigin'

export function sourceRoute(projectId: string, source: TaskSource): RouteLocationRaw {
  const query = { block: source.block_id }
  if (source.where === 'thread') {
    return {
      name: 'workspace-thread',
      params: { projectId, topicId: source.room_id, threadId: source.conversation_id },
      query,
    }
  }
  if (source.where === 'task') {
    return {
      name: 'workspace-task',
      params: { projectId, topicId: source.room_id, taskId: source.conversation_id },
      query,
    }
  }
  return { name: 'workspace-topic', params: { projectId, topicId: source.conversation_id }, query }
}
