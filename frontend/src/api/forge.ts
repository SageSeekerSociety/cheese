import type {
  BranchProtection,
  BranchProtectionPatch,
  BranchProtectionRules,
  ForgeAttribution,
  ForgeConnection,
  GithubConnection,
  OAuthConnectionInfo,
  UpstreamInfo,
} from '../cx_types'

import { request } from './http'
import { legacyRequest } from './legacy'

// 上游仓库 (spec §6.3): link an existing git repo and pull its history in.
export function getUpstream(projectId: string): Promise<UpstreamInfo> {
  return request<UpstreamInfo>(`/projects/${encodeURIComponent(projectId)}/upstream`)
}
export function setUpstream(projectId: string, url: string): Promise<UpstreamInfo> {
  return request(`/projects/${encodeURIComponent(projectId)}/upstream`, {
    method: 'PUT',
    body: JSON.stringify({ url }),
  })
}

// 分支保护 (#718): 平台侧的合并规则。GET 附带只读的 merge_method 和
// github_protection；PUT 是 partial-update，body 里出现哪个键就改哪个。
export function getBranchProtection(projectId: string): Promise<BranchProtection> {
  return request<BranchProtection>(`/projects/${encodeURIComponent(projectId)}/branch-protection`)
}
export function setBranchProtection(projectId: string, patch: BranchProtectionPatch): Promise<BranchProtectionRules> {
  return request(`/projects/${encodeURIComponent(projectId)}/branch-protection`, {
    method: 'PUT',
    body: JSON.stringify(patch),
  })
}

export function getForgeConnection(projectId: string): Promise<ForgeConnection> {
  return request(`/projects/${encodeURIComponent(projectId)}/forge`)
}

export function getForgeAttribution(projectId: string): Promise<ForgeAttribution> {
  return request(`/projects/${encodeURIComponent(projectId)}/forge-attribution`)
}

export function setForgeAttribution(projectId: string, requesterCoauthor: boolean | null): Promise<ForgeAttribution> {
  return request(`/projects/${encodeURIComponent(projectId)}/forge-attribution`, {
    method: 'PUT',
    body: JSON.stringify({ requester_coauthor: requesterCoauthor }),
  })
}

// GitHub App install flow (#192).
export function getGithubConnection(projectId: string): Promise<GithubConnection> {
  return request(`/projects/${encodeURIComponent(projectId)}/github/connection`)
}
export function getGithubInstallUrl(projectId: string): Promise<{ url: string }> {
  return request(`/projects/${encodeURIComponent(projectId)}/github/install-url`)
}
// Connect via an existing cheesex-app installation when one already covers the
// upstream repo; {connected:false, install_url} means "go through GitHub".
// (GitHub's install page never fires the callback when the App is already
// installed, so the frontend must try this first.)
export function connectGithubRepo(
  projectId: string
): Promise<{ connected: boolean; repo?: string; account?: string; install_url?: string }> {
  return request(`/projects/${encodeURIComponent(projectId)}/github/connect`, { method: 'POST' })
}
export function getGithubAccountAuthorizeUrl(projectId: string): Promise<{ url: string }> {
  return request(`/users/me/github-account/authorize-url?return_project_id=${encodeURIComponent(projectId)}`)
}

// Personal OAuth/App connections (1.0 router, see legacyRequest): every
// provider the user has linked, not just github_app; callers filter by providerId.
export function listOAuthConnections(userId: string): Promise<{ connections: OAuthConnectionInfo[] }> {
  return legacyRequest(`/users/${encodeURIComponent(userId)}/oauth/connections`)
}
// Spends a sudo ticket minted for 'oauth:unbind' (see utils/sudo.ts).
export function deleteOAuthConnection(userId: string, connectionId: number, sudoTicket: string): Promise<void> {
  return legacyRequest(`/users/${encodeURIComponent(userId)}/oauth/connections/${connectionId}`, {
    method: 'DELETE',
    body: JSON.stringify({ sudoTicket }),
  })
}
