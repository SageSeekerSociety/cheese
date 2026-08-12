import type { OAuthConnectionInfo } from '../cx_types'

import { describe, expect, it } from 'vitest'

import { findGithubAccountConnection, isGithubAccountTokenExpired } from './githubAccount'

function conn(overrides: Partial<OAuthConnectionInfo> = {}): OAuthConnectionInfo {
  return {
    id: 1,
    providerId: 'github_app',
    providerName: 'GitHub (cheesex-app)',
    providerUserId: '12345',
    connectedAt: '2026-08-01T00:00:00Z',
    login: 'octocat',
    tokenExpires: null,
    hasRefreshToken: true,
    ...overrides,
  }
}

describe('findGithubAccountConnection', () => {
  it('picks the github_app connection among other providers', () => {
    const github = conn()
    const google = conn({ id: 2, providerId: 'google', providerName: 'Google' })
    expect(findGithubAccountConnection([google, github])).toBe(github)
  })

  it('returns null when github_app is not connected', () => {
    const google = conn({ id: 2, providerId: 'google', providerName: 'Google' })
    expect(findGithubAccountConnection([google])).toBeNull()
    expect(findGithubAccountConnection([])).toBeNull()
  })
})

describe('isGithubAccountTokenExpired', () => {
  const now = new Date('2026-08-10T12:00:00Z')

  it('is not expired when tokenExpires is null (App has no expiry configured)', () => {
    expect(isGithubAccountTokenExpired(conn({ tokenExpires: null }), now)).toBe(false)
  })

  it('is expired when tokenExpires is in the past', () => {
    expect(isGithubAccountTokenExpired(conn({ tokenExpires: '2026-08-01T00:00:00Z' }), now)).toBe(true)
  })

  it('is not expired when tokenExpires is in the future', () => {
    expect(isGithubAccountTokenExpired(conn({ tokenExpires: '2026-12-01T00:00:00Z' }), now)).toBe(false)
  })

  it('treats an unparsable tokenExpires as not expired rather than crashing', () => {
    expect(isGithubAccountTokenExpired(conn({ tokenExpires: 'not-a-date' }), now)).toBe(false)
  })
})
