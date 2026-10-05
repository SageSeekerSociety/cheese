import type { BoardPhrase } from '../cx_types'

/** 待我处理清单里的一件事（后端 `room_task/awaiting.py`）。
 *
 *  `phrase` 就是看板卡面上那一句的码，后端算好的 —— 前端不推状态，理由和
 *  `Presentation` 那一段一样。`reason` 说的是这件事为什么点到我：递给我验收
 *  (`reviewer`)、我提的需求有了结果 (`reporter`)、或者芝士停在一个只有我能回答的
 *  待回答的问题上 (`asked`)。 */
export interface WaitingItem {
  projectId: string
  projectName: string
  topicId: string
  topicTitle: string
  taskId: string | null
  taskTitle: string | null
  taskTitleSource?: string | null
  phrase: BoardPhrase
  reason: 'reviewer' | 'reporter' | 'asked'
  blockId?: string | null
  at: string
}
