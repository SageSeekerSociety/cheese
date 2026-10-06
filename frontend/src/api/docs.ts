import { request } from './http'

/** A 30-second, one-use grant that signs this person in to the docs site, and where to post it. */
export function requestDocsGrant(): Promise<{ url: string; grant: string }> {
  return request<{ url: string; grant: string }>('/docs/grant', { method: 'POST' })
}
