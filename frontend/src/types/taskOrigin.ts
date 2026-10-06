// 任务从哪来（`GET /topics/{task}/related`）：转出它的那段讨论，和讨论里摆出来的
// 文档、文件。任务页上叫「相关」。
export interface TaskOriginMessage {
  block_id: string
  author: string
  content: string
  created_at: string
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
  origin: TaskOrigin | null
  materials: TaskMaterial[]
}
