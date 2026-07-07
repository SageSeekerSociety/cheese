// 知是 2.0 群聊/私聊 API — typed veneer over the backend `/connector` thread contract.
//
// These are *browser-side* connector calls: they address the backend directly at
// `/connector/...` (proxied to :8080 in dev, served at root in prod), NOT via the
// `/api` REST base, and they return raw JSON (no `{code,message,data}` envelope).
// Actor = the JWT user_id, injected server-side; requests never carry a user id.

import { connectorGet, connectorSend } from './connectorFetch'

// ── identity ────────────────────────────────────────────────────────────────

// `Member` / `UserRef` — reused everywhere. `is_agent` is derived from an execution
// binding server-side; nickname/avatar_id come from the user's profile (agents are
// users too). Fields past `is_agent` are only present in the contexts noted below.
export interface Member {
  user_id: number
  nickname: string
  avatar_id: number | null
  is_agent: boolean
  role?: number // 0 member / 1 admin / 2 owner — only in member lists
  agent_status?: string | null
  elapsed?: string | null
  tokens?: string | null
  sid?: string | null // is_agent && online — used to open 现场
  device_id?: string | null
  last_read_block_id?: number // read high-water mark — only in member lists (已阅 pie)
}

export type UserRef = Member

export interface LastMessage {
  text: string
  ts: string
  author_id: number
}

// kind: 0 general / 1 direct / 2 mgmt
export interface Thread {
  id: number
  kind: number
  title: string | null
  project_id: number | null
  member_count: number
  last_message?: LastMessage | null
}

export interface Message {
  id: number
  thread_id: number
  author_id: number
  author: UserRef
  text: string
  ts: string
  reply_to_id?: number | null
}

export interface ThreadApplication {
  id: number
  thread_id: number
  thread_title: string | null
  user_id: number
  user: UserRef
  initiator_id: number
  type: 'INVITATION' | 'REQUEST'
  status: string
  role: number
  message?: string | null
  created_at: string
}

// Result of POST .../members — human added immediately, agent turns into an invite.
export type AddMemberResult =
  | { added: true; member: Member }
  | { pending: true; application_id: number }

// Attention policy for an agent member in a thread.
// mode: ALL 立即处理所有消息 / INTERVAL 间隔≥N分钟 / MENTION 仅当@时（默认）
export type AttentionMode = 'ALL' | 'INTERVAL' | 'MENTION'
export interface AttentionAgent {
  user_id: number
  nickname: string
  avatar_id: number | null
  mode: AttentionMode
  interval_minutes?: number
}

// ── transport ───────────────────────────────────────────────────────────────
// Raw-fetch connector transport with refresh-on-401 lives in ./connectorFetch so chat,
// presence and 我的 Agent all share one refresh path (see that file for the why).

const get = connectorGet
const send = connectorSend

// Batch presence lookup: for each user_id, is it an agent and (if so) its live status +
// how to open its 现场. Powers the site-wide agent-aware avatar.
export async function fetchPresence(ids: number[]): Promise<Record<number, Member>> {
  if (!ids.length) return {}
  const r = await get<{ presence: Record<string, Member> }>(
    `/connector/agents/presence?ids=${ids.join(',')}`,
  )
  const out: Record<number, Member> = {}
  for (const [k, v] of Object.entries(r.presence)) out[Number(k)] = v
  return out
}

// ── calls ─────────────────────────────────────────────────────────────────────

export const ThreadsApi = {
  listThreads: () => get<{ threads: Thread[] }>(`/connector/threads`),

  createThread: (data: { title: string; member_ids?: number[] }) =>
    send<{ thread: Thread }>('POST', `/connector/threads`, data),

  getThread: (tid: number) => get<{ thread: Thread; members: Member[] }>(`/connector/threads/${tid}`),

  renameThread: (tid: number, title: string) =>
    send<{ thread: Thread }>('PATCH', `/connector/threads/${tid}`, { title }),

  listMessages: (tid: number, after = 0) =>
    get<{ messages: Message[] }>(`/connector/threads/${tid}/messages?after=${after}`),

  postMessage: (tid: number, text: string, mention_user_ids?: number[]) =>
    send<{ message: Message; forwarded_to_agents: number }>('POST', `/connector/threads/${tid}/messages`, {
      text,
      ...(mention_user_ids && mention_user_ids.length ? { mention_user_ids } : {}),
    }),

  // Advance the caller's read high-water mark (Feishu 已阅). Monotonic server-side.
  markRead: (tid: number, last_read_id: number) =>
    send<{ ok: boolean; last_read_block_id: number }>('POST', `/connector/threads/${tid}/read`, {
      last_read_id,
    }),

  listMembers: (tid: number) => get<{ members: Member[] }>(`/connector/threads/${tid}/members`),

  addMember: (tid: number, user_id: number, role?: number) =>
    send<AddMemberResult>('POST', `/connector/threads/${tid}/members`, { user_id, role }),

  removeMember: (tid: number, user_id: number) =>
    send<{ removed: true }>('DELETE', `/connector/threads/${tid}/members/${user_id}`),

  // 任务3 — 角色（提升/降级）；仅群主
  changeRole: (tid: number, user_id: number, role: number) =>
    send<{ member: Member }>('POST', `/connector/threads/${tid}/members/${user_id}/role`, { role }),

  // 任务2 — 解散群；仅群主
  deleteThread: (tid: number) => send<{ deleted: true }>('DELETE', `/connector/threads/${tid}`),

  // 任务4 — 本群「已邀请成员」（群主/管理员）
  listThreadApplications: (tid: number) =>
    get<{ applications: ThreadApplication[] }>(`/connector/threads/${tid}/applications`),

  cancelApplication: (tid: number, app_id: number) =>
    send<{ canceled: true }>('DELETE', `/connector/threads/${tid}/applications/${app_id}`),

  // 任务5 — 关注频率（任何群成员可看/改）
  listAttention: (tid: number) =>
    get<{ agents: AttentionAgent[] }>(`/connector/threads/${tid}/attention`),

  setAttention: (tid: number, agent_user_id: number, body: { mode: AttentionMode; interval_minutes?: number }) =>
    send<{ agent: AttentionAgent }>('POST', `/connector/threads/${tid}/attention/${agent_user_id}`, body),

  listCandidates: (tid: number, q = '') =>
    get<{ candidates: UserRef[] }>(`/connector/threads/${tid}/candidates?q=${encodeURIComponent(q)}`),

  // 审批（approver 视角）
  listApplications: () => get<{ applications: ThreadApplication[] }>(`/connector/thread-applications`),

  approveApplication: (id: number) =>
    send<{ application: ThreadApplication }>('POST', `/connector/thread-applications/${id}/approve`),

  rejectApplication: (id: number) =>
    send<{ application: ThreadApplication }>('POST', `/connector/thread-applications/${id}/reject`),
}

// Helpers shared by chat UI.
export function threadDisplayTitle(t: Thread, selfUserId: number | null, members?: Member[]): string {
  if (t.title) return t.title
  if (t.kind === 1 && members) {
    const peer = members.find((m) => m.user_id !== selfUserId)
    if (peer) return peer.nickname
  }
  return t.kind === 1 ? '私聊' : '群聊'
}

export function isAgentWorking(status?: string | null): boolean {
  return !!status && status !== 'idle' && status !== 'offline'
}
