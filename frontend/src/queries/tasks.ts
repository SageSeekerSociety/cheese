// 一个项目的任务：侧栏挂在频道下面的、项目总览的、「全部任务」的，读的是这里。
import type { TaskFilter } from '@/api/tasks'
import type { ListPayload, RoomTask } from '@/cx_types'

import { infiniteQueryOptions, queryOptions } from '@tanstack/vue-query'

import { listProjectTasks, pageProjectTasks } from '@/api'
import { queryClient } from '@/lib/queryClient'
import { keys } from '@/queries/keys'

/**
 * 项目里还开着的任务。整个项目的任务后端每读一次都要全部算一遍，而清单没变的时候
 * 服务端答 304，手上这份原样留着（见 `readSince`）。
 */
export function openProjectTasksQuery(projectId: string) {
  const queryKey = keys.projectOpenTasks(projectId)
  return queryOptions({
    queryKey,
    queryFn: (): Promise<ListPayload<RoomTask>> =>
      listProjectTasks(projectId, { open: true }, queryClient.getQueryData(queryKey)),
  })
}

/** 「全部任务」里已经结束的，往下滚到哪读到哪。 */
export const CLOSED_PAGE = 50

/** 已经结束的任务，最近结束的在前，一页一页读；频道、谁的在服务端筛，计数也是服务端数的。 */
export function closedProjectTasksQuery(projectId: string, filter: { channel: string | null; whose: TaskFilter }) {
  return infiniteQueryOptions({
    queryKey: keys.projectClosedTasks(projectId, filter),
    queryFn: ({ pageParam }) =>
      pageProjectTasks(projectId, {
        status: 'closed',
        limit: CLOSED_PAGE,
        before: pageParam,
        channel: filter.channel,
        whose: filter.whose === 'all' ? null : filter.whose,
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => (last.has_more ? last.next : null),
  })
}
