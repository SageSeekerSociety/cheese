// 项目资料库里的一份文档，接口答的样子（`api/projectDocuments.ts`）。放在这里，组件
// 只认形状，不认接口。

/** 一份文档，不含正文。`topic_id` 有值的是某个对话自带的那一份。 */
export interface ProjectDocument {
  id: string
  project_id: string
  topic_id: string | null
  kind: string
  title: string | null
  doc_version: number
  author: string
  created_at: string
  updated_at: string
}

/** 搜索命中的一份：正文里命中的那一段。对话里的那几份还带着对话的名字。 */
export interface DocumentHit extends ProjectDocument {
  snippet: string
  room_title?: string
  room_title_source?: string
}

export interface DocumentSearch {
  query: string
  library: DocumentHit[]
  rooms: DocumentHit[]
}
