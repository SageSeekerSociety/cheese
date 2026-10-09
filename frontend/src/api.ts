// The API layer's public surface. Every function lives in a module under
// src/api/ named for its domain; this file only re-exports them so the
// existing `@/api` importers (and their `vi.mock('@/api')`) keep resolving here.
// Nothing but `export … from` statements belongs in this file.
export * from './api/accept'
export * from './api/admin/gateway'
export * from './api/admin/platformAdmins'
export * from './api/admin/stats'
export * from './api/admin/statsProduct'
export type { AgentControlResult, AgentControlState } from './api/agentControl'
export { getAgentControl, sendAgentControl } from './api/agentControl'
export * from './api/agents'
export * from './api/alerts'
export * from './api/artifacts'
export * from './api/blocks'
export * from './api/compute'
export * from './api/devices'
export * from './api/environment'
export * from './api/feedback'
export * from './api/forge'
export {
  ApiError,
  authToken,
  BASE,
  ensureFreshToken,
  isEndpointMissing,
  isRetryableGetFailure,
  NotModified,
  READ_BUDGET_MS,
  readSince,
  refreshNow,
  request,
  RequestTimeoutError,
  tokenExpiresWithin,
} from './api/http'
export * from './api/integrations'
export * from './api/library'
export * from './api/mcp'
export * from './api/members'
export * from './api/memory'
export * from './api/people'
export { requestPreviewSession } from './api/preview'
export * from './api/progress'
export * from './api/projects'
export * from './api/push'
export * from './api/roomFiles'
export * from './api/tasks'
export { addTopicMember, joinChannel, leaveChannel, removeTopicMember, setChannelDescription } from './api/topicMembers'
export * from './api/topicReads'
export * from './api/topics'
export * from './api/workPanel'
export * from './api/ws'
export { PreviewRendererUnavailable } from './lib/previewPdf'
export { TOPIC_TITLE_MAX_LENGTH } from './lib/topicTitle'
export type { PreviewSelection, PreviewSession } from './types/preview'
export type { StatsClaudeAccount, StatsClaudePool, StatsUsage } from './types/statsUsage'
