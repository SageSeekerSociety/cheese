// 任务从哪来（`GET /topics/{task}/related`）：它是从哪句话来的（任务页顶上引的那句），
// 转出它的那段讨论，和讨论里摆出来的文档、文件（任务页上叫「相关」）。
export interface TaskOriginMessage {
  block_id: string
  author: string
  content: string
  created_at: string
}

/** 任务是从哪句话来的：转成它的那条消息，或 AI 队友建它时在回的那句人话。 */
export interface TaskSource extends TaskOriginMessage {
  /** 这句话在哪段对话里：频道主线、支线，或另一个任务。点它就去那里、停在这一条上。 */
  conversation_id: string
  room_id: string
  where: 'channel' | 'thread' | 'task'
  /** 频道名（主线和支线），或那个任务的标题。 */
  title: string
}

export interface TaskOrigin {
  /** 讨论在哪段对话里：支线，或频道主线。 */
  conversation_id: string
  room_id: string
  /** 支线挂着的那条消息；从主线上提议转出的没有。 */
  root: TaskOriginMessage | null
  reply_count: number
}

export type TaskMaterial =
  | { kind: 'document'; id: string; title: string; by: string }
  | { kind: 'file'; path: string; by: string }

export interface TaskRelated {
  /** 自己新建的任务没有。 */
  source: TaskSource | null
  origin: TaskOrigin | null
  materials: TaskMaterial[]
}
