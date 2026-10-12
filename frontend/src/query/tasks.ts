// 一个项目的任务：侧栏挂在频道下面的、项目总览的、「全部任务」的，读的是这里。
import type { TaskFilter } from '@/api/tasks'
import type { ListPayload, RoomTask } from '@/cx_types'

import { infiniteQueryOptions, queryOptions } from '@tanstack/vue-query'

import { listProjectTasks, pageProjectTasks } from '@/api'
import { queryClient } from '@/query/client'
import { keys } from '@/query/keys'

/**
 * 项目里还开着的任务。整个项目的任务后端每读一次都要全部算一遍，而清单没变的时候
 * 服务端答 304，手上这份原样留着（见 `readSince`）。
 */
export function openProjectTasksQuery(projectId: string) {
  const queryKey = keys.projectOpenTasks(projectId)
  return queryOptions({
    queryKey,
    queryFn: ({ signal }): Promise<ListPayload<RoomTask>> =>
      listProjectTasks(projectId, { open: true }, queryClient.getQueryData(queryKey), signal),
  })
}

/** 「全部任务」里一页页读的那两份（已结束的、已归档的），往下滚到哪读到哪。 */
export const CLOSED_PAGE = 50

/** 已经结束的、或者被收起来的任务，一页一页读；频道、谁的在服务端筛，计数也是服务端
 *  数的。两份各是各的清单：收起来的不出现在「已关闭」里。 */
export function pagedProjectTasksQuery(
  projectId: string,
  status: 'closed' | 'archived',
  filter: { channel: string | null; whose: TaskFilter }
) {
  return infiniteQueryOptions({
    queryKey: keys.projectPagedTasks(projectId, status, filter),
    queryFn: ({ pageParam }) =>
      pageProjectTasks(projectId, {
        status,
        limit: CLOSED_PAGE,
        before: pageParam,
        channel: filter.channel,
        whose: filter.whose === 'all' ? null : filter.whose,
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => (last.has_more ? last.next : null),
  })
}
