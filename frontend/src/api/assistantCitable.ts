// 芝士输入框里 @ 能引用的东西（`/assistant/citable`）：这个人参加的项目的频道和任务、
// 这些项目和他所在团队的文档、眼前这个项目和他自己的资料库里的文件。只列他打得开的。
// 题目不在这里列，打字时用 `/tasks/search` 搜。

import type { Citable } from '../types/citable'

import { request } from './http'

export type { Citable, CitableKind } from '../types/citable'

export function getCitable(projectId: string | null): Promise<{ items: Citable[] }> {
  const query = projectId ? `?project=${encodeURIComponent(projectId)}` : ''
  return request<{ items: Citable[] }>(`/assistant/citable${query}`)
}
