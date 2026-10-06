// 项目里的内容搜索：消息、文档、任务、资料库，只搜这个人能看的房间。
import type { RoomTask } from '../cx_types'

import { request } from './http'

/** 项目里一次搜索的结果：只搜这个人能看的房间，每组最相关的在前。 */
export interface ProjectSearchHits {
  records: {
    id: string
    room_id: string
    room_title: string
    kind: 'message' | 'doc' | 'doc_node' | 'comment' | 'weekly'
    author: string
    /** 作者现在叫什么：人的昵称，队友在这个项目里的名字。没有时为 null，界面写 handle。 */
    author_name: string | null
    /** 队友的名字还是不是出生时那个（`default`，见 `teammateName`）；人为 null。 */
    author_name_source: string | null
    created_at: string
    /** 说在某件活的卡片里，而不是房间自己的对话里。 */
    task_id: string | null
    snippet: string
  }[]
  tasks: (Pick<RoomTask, 'id' | 'room_id' | 'title' | 'title_source' | 'status'> & {
    room_title: string
    snippet: string
  })[]
  library: { path: string; bytes: number; modified: string }[]
}

/**
 * `only` 只搜这几类（`message`、`doc_node`…、`tasks`、`library`），并且可以用 `offset`
 * 往后翻；不给 `only` 就是每类各取前 `limit` 条。
 */
export async function searchProject(
  projectId: string,
  q: string,
  limit = 10,
  page?: { only: string[]; offset: number }
): Promise<ProjectSearchHits> {
  return (await askProjectSearch(projectId, q, limit, page, false)).hits
}

/**
 * 同一次搜索，再带上每一类各能搜到多少（`message`、`doc`、`doc_node`、`comment`、
 * `weekly`、`tasks`、`library`）。搜索结果页第一次打开时用它，一次问完。
 */
export async function searchProjectCounted(
  projectId: string,
  q: string,
  limit: number,
  page?: { only: string[]; offset: number }
): Promise<{ hits: ProjectSearchHits; counts: Record<string, number> }> {
  const body = await askProjectSearch(projectId, q, limit, page, true)
  return { hits: body.hits, counts: body.counts ?? {} }
}

function askProjectSearch(
  projectId: string,
  q: string,
  limit: number,
  page: { only: string[]; offset: number } | undefined,
  withCounts: boolean
): Promise<{ hits: ProjectSearchHits; counts?: Record<string, number> }> {
  const params = new URLSearchParams({ q, limit: String(limit) })
  if (page) {
    for (const kind of page.only) params.append('only', kind)
    params.set('offset', String(page.offset))
  }
  if (withCounts) params.set('with_counts', 'true')
  return request(`/projects/${encodeURIComponent(projectId)}/context/search?${params}`)
}
