// 人说得出口的地址：`/projects/<短名>/tasks/318`。浏览器拿短名和编号换出各接口要的
// UUID，也拿 UUID 换回短地址。规则在后端 `app/domain/project/address.py`。
import { request } from './http'

/** 地址里的三种编号，值就是地址里那一段。 */
export type NumberedKind = 'tasks' | 'docs' | 'channels'

export type ProjectAddress = { id: string; slug: string }

export type ThingAddress = {
  project_id: string
  slug: string
  kind: NumberedKind
  id: string
  /** 任务所在的频道；频道就是它自己；文档没有。 */
  room_id: string | null
  /** 还没编号（或者永远不编号：私聊、任务自己的文档）时是 null。 */
  number: number | null
}

/** 短名、改名前的旧短名或 UUID 指向的项目；`slug` 是它现在的短名。 */
export function resolveProject(ref: string): Promise<ProjectAddress> {
  return request<ProjectAddress>(`/addresses/projects/${encodeURIComponent(ref)}`)
}

export function resolveNumber(ref: string, kind: NumberedKind, number: string | number): Promise<ThingAddress> {
  return request<ThingAddress>(
    `/addresses/projects/${encodeURIComponent(ref)}/${kind}/${encodeURIComponent(String(number))}`
  )
}

export function addressOf(kind: NumberedKind, id: string): Promise<ThingAddress> {
  return request<ThingAddress>(`/addresses/of/${kind}/${encodeURIComponent(id)}`)
}

export function setProjectSlug(projectId: string, slug: string): Promise<ProjectAddress> {
  return request<ProjectAddress>(`/projects/${encodeURIComponent(projectId)}/slug`, {
    method: 'PUT',
    body: JSON.stringify({ slug }),
  })
}
