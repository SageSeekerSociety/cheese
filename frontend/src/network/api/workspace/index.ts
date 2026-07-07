// 知是 2.0 workspace API — 群聊 / 文档 / 事项.
// Thin typed veneer over the backend's Phase-A contract. The actor is injected
// server-side from the auth token; requests never carry a user id.

import { NewApiInstance } from '../index'

// ── shared ────────────────────────────────────────────────────────────────────

export type AgentStatus = 'idle' | 'working'

export interface UserSummary {
  id: number
  username: string
  nickname: string
  avatarId: number | null
  isAgent: boolean
  agentStatus: AgentStatus | null
}

export type RefTargetType = 'block' | 'document' | 'workitem'

export interface BlockRef {
  targetType: RefTargetType
  targetId: number
  title?: string | null
}

export interface Page {
  pageStart: number | null
  pageSize: number
  hasMore: boolean
  nextStart: number | null
}

// ── 群聊 ─────────────────────────────────────────────────────────────────────

export type ThreadKind = 'general' | 'management'
export type AttentionPolicy = 'ALL_MESSAGES' | 'ALL_USER_MESSAGES' | 'IDLE_WINDOW' | 'MENTION_ONLY'
export type MessageKind = 'text' | 'system'

export interface Message {
  id: number
  threadId: number
  author: UserSummary
  kind: MessageKind
  replyToId: number | null
  content: string
  refs: BlockRef[]
  createdAt: number
  editedAt: number | null
}

export interface ThreadMember {
  user: UserSummary
  role: string
  attentionPolicyOverride: AttentionPolicy | null
}

export interface Thread {
  id: number
  projectId: number
  parentThreadId: number | null
  kind: ThreadKind
  title: string
  memberCount: number
  members: UserSummary[]
  lastMessage: Message | null
  unread: number
  updatedAt: number
}

// ── 文档 ─────────────────────────────────────────────────────────────────────

export type DocNodeKind = 'heading' | 'paragraph' | 'bullet' | 'todo' | 'code'

export interface DocNode {
  id: number
  documentId: number
  structParentId: number | null
  kind: DocNodeKind
  content: string
  order: number
  editedAt: number | null
  editedBy: UserSummary | null
}

export interface Document {
  id: number
  projectId: number
  parentId: number | null
  title: string
  updatedAt: number
  updatedBy: UserSummary | null
}

export interface DocumentDetail {
  document: Document
  nodes: DocNode[]
}

// ── 事项 ─────────────────────────────────────────────────────────────────────

export type WorkItemStatus = 'open' | 'in_progress' | 'done' | 'blocked'

export interface WorkItem {
  id: number
  projectId: number
  parentId: number | null
  title: string
  description: string
  owner: UserSummary | null
  status: WorkItemStatus
  locked: boolean
  annotationCount: number
  createdAt: number
  updatedAt: number
}

export interface WorkItemAnnotation {
  id: number
  workItemId: number
  author: UserSummary
  content: string
  createdAt: number
}

export interface WorkItemDetail {
  workItem: WorkItem
  annotations: WorkItemAnnotation[]
}

// ── calls ─────────────────────────────────────────────────────────────────────

export namespace WorkspaceApi {
  // 群聊
  export const listThreads = (projectId: number) =>
    NewApiInstance.request<{ threads: Thread[] }>({ url: `/projects/${projectId}/threads`, method: 'GET' })

  export const createThread = (projectId: number, data: { title: string; kind?: ThreadKind; memberIds?: number[] }) =>
    NewApiInstance.request<{ thread: Thread }>({ url: `/projects/${projectId}/threads`, method: 'POST', data })

  export const getThread = (threadId: number) =>
    NewApiInstance.request<{ thread: Thread }>({ url: `/threads/${threadId}`, method: 'GET' })

  export const listMessages = (threadId: number, params?: { pageStart?: number; pageSize?: number }) =>
    NewApiInstance.request<{ messages: Message[]; page: Page }>({ url: `/threads/${threadId}/messages`, method: 'GET', params })

  export const postMessage = (threadId: number, data: { content: string; replyToId?: number; refs?: BlockRef[] }) =>
    NewApiInstance.request<{ message: Message }>({ url: `/threads/${threadId}/messages`, method: 'POST', data })

  export const listMembers = (threadId: number) =>
    NewApiInstance.request<{ members: ThreadMember[] }>({ url: `/threads/${threadId}/members`, method: 'GET' })

  // 文档
  export const listDocuments = (projectId: number) =>
    NewApiInstance.request<{ documents: Document[] }>({ url: `/projects/${projectId}/documents`, method: 'GET' })

  export const createDocument = (projectId: number, data: { title: string; parentId?: number }) =>
    NewApiInstance.request<{ document: Document }>({ url: `/projects/${projectId}/documents`, method: 'POST', data })

  export const getDocument = (documentId: number) =>
    NewApiInstance.request<DocumentDetail>({ url: `/documents/${documentId}`, method: 'GET' })

  export const editNode = (documentId: number, nodeId: number, data: { content: string }) =>
    NewApiInstance.request<{ node: DocNode }>({ url: `/documents/${documentId}/nodes/${nodeId}`, method: 'PUT', data })

  // 事项
  export const listWorkItems = (projectId: number) =>
    NewApiInstance.request<{ workItems: WorkItem[] }>({ url: `/projects/${projectId}/workitems`, method: 'GET' })

  export const createWorkItem = (projectId: number, data: { title: string; description?: string; parentId?: number }) =>
    NewApiInstance.request<{ workItem: WorkItem }>({ url: `/projects/${projectId}/workitems`, method: 'POST', data })

  export const getWorkItem = (workItemId: number) =>
    NewApiInstance.request<WorkItemDetail>({ url: `/workitems/${workItemId}`, method: 'GET' })

  export const claimWorkItem = (workItemId: number) =>
    NewApiInstance.request<{ workItem: WorkItem }>({ url: `/workitems/${workItemId}/claim`, method: 'POST' })

  export const updateStatus = (workItemId: number, data: { status: WorkItemStatus }) =>
    NewApiInstance.request<{ workItem: WorkItem }>({ url: `/workitems/${workItemId}`, method: 'PATCH', data })

  export const addAnnotation = (workItemId: number, data: { content: string }) =>
    NewApiInstance.request<{ annotation: WorkItemAnnotation }>({ url: `/workitems/${workItemId}/annotations`, method: 'POST', data })
}
