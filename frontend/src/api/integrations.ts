import type { ListPayload } from '../cx_types'

import { request } from './http'

export interface Integration {
  id: string
  provider: 'mail' | 'feishu'
  label: string
  owner_handle: string
  config: Record<string, unknown>
  grants: string[]
  status: 'ok' | 'auth_failed' | 'unreachable' | 'error'
  last_error: string
  last_checked_at: string | null
  user_authorized: boolean
  /** 用的是平台管理员配的那一个应用，而不是这条连接自己带的凭据。 */
  shared_app: boolean
}

export interface MailDraft {
  id: string
  integration_id: string
  project_id: string
  topic_id: string | null
  created_by: string
  to: string[]
  cc: string[]
  subject: string
  body: string
  attachments: { path: string; name: string; size: number }[]
  status: 'drafted' | 'sent' | 'failed' | 'discarded'
  error: string
  sent_at: string | null
  created_at: string | null
}

export function listMyIntegrations(): Promise<ListPayload<Integration>> {
  return request<ListPayload<Integration>>('/me/integrations')
}

export function connectMail(body: Record<string, unknown>): Promise<Integration> {
  return request<Integration>('/me/integrations/mail', { method: 'POST', body: JSON.stringify(body) })
}

export function updateIntegration(id: string, body: Record<string, unknown>): Promise<Integration> {
  return request<Integration>(`/me/integrations/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
}

export function checkIntegration(id: string): Promise<Integration> {
  return request<Integration>(`/me/integrations/${encodeURIComponent(id)}/check`, { method: 'POST' })
}

export function deleteIntegration(id: string): Promise<{ deleted: string }> {
  return request<{ deleted: string }>(`/me/integrations/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

export function listMyMailDrafts(status: string): Promise<ListPayload<MailDraft>> {
  return request<ListPayload<MailDraft>>(`/me/mail-drafts?status=${encodeURIComponent(status)}`)
}

export function sendMailDraft(id: string): Promise<{ draft: MailDraft; refused: string[]; notes: string[] }> {
  return request<{ draft: MailDraft; refused: string[]; notes: string[] }>(
    `/me/mail-drafts/${encodeURIComponent(id)}/send`,
    { method: 'POST' }
  )
}

export function discardMailDraft(id: string): Promise<MailDraft> {
  return request<MailDraft>(`/me/mail-drafts/${encodeURIComponent(id)}/discard`, { method: 'POST' })
}
