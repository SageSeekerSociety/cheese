// 项目资料库里的一份文档，接口答的样子（`api/projectDocuments.ts`）。放在这里，组件
// 只认形状，不认接口。

/** 一份文档，不含正文。 */
export interface ProjectDocument {
  id: string
  project_id: string
  // 在项目里的编号，地址 `/projects/<短名>/docs/<编号>` 用它。
  number?: number | null
  kind: string
  title: string | null
  doc_version: number
  author: string
  created_at: string
  updated_at: string
}

/** 搜索命中的一份：正文里命中的那一段。任务的文档和项目总览还带着它在哪个频道、
 *  哪个任务，以及任务（或频道）的名字。 */
export interface DocumentHit extends ProjectDocument {
  snippet: string
  room_id?: string
  task_id?: string | null
  room_title?: string
}

export interface DocumentSearch {
  query: string
  library: DocumentHit[]
  rooms: DocumentHit[]
}
