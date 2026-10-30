// 房间 socket 上客户端说的话，和它要来的那一帧现场。别的服务端帧在 cx_types 的
// WsServerFrame 里。
import type { MemberActivity } from '@/lib/memberActivity'

// The client sends the liveness probe, `typing` (I am composing here; `active:
// false` = stopped; who is the socket's credential) and `sync` — a message is a POST.
export type WsClientMessage = { type: 'ping' } | { type: 'typing'; active?: boolean } | { type: 'sync' }

/** 后端还欠着的一次自动重试，`room_state` 帧里的那一项（turn_resume.waiting）。 */
export interface ResumeWait {
  conversation_id: string
  seat: string
  resume_id: string
  /** 约定重试的时刻，ISO。 */
  due_at: string
  /** 那一轮没完成的时刻，ISO：从它起算在等。 */
  since: string
}

/**
 * 回答客户端的 `sync`：此刻在跑的全部轮次和在忙的全部成员，空的也发。重连后屏幕上
 * 留着断线前的现场，拿它核对：断线期间结束的撤掉，其余原样留着
 * （components/room/composables/roomResync）。
 */
export interface RoomStateFrame {
  type: 'room_state'
  turn_ids: string[]
  /** 每个轮次的开始时刻，epoch 秒。 */
  since?: Record<string, number>
  /** 每个轮次在哪个座位上，键是 turn_id。 */
  agents?: Record<string, string>
  members: MemberActivity[]
  /** 此刻还欠着的自动重试：哪位队友、约几点重试。 */
  resumes: ResumeWait[]
}
