import type { OAuthConnectionInfo } from '../cx_types'

import { describe, expect, it } from 'vitest'

import {
  explainAccountLinkFailure,
  explainRepoInstallFailure,
  findGithubAccountConnection,
  isGithubAccountTokenExpired,
} from './githubAccount'

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

describe('explainAccountLinkFailure', () => {
  it('turns every code the account callback can emit into a sentence', () => {
    // The full set, read off github_account_link.py — a code missing here is a
    // user staring at an English identifier (the #222 incident).
    for (const code of ['already_linked', 'oauth_failed', 'github_unreachable', 'invalid_state']) {
      const text = explainAccountLinkFailure(code)
      expect(text).not.toContain(code)
      expect(text.length).toBeGreaterThan('连接 GitHub 账号失败：'.length + 8)
    }
  })

  it('says what to do, not just what broke', () => {
    expect(explainAccountLinkFailure('already_linked')).toContain('断开')
    expect(explainAccountLinkFailure('invalid_state')).toContain('重新点')
  })

  it('does not blame the person when our server could not reach GitHub', () => {
    const text = explainAccountLinkFailure('github_unreachable')
    expect(text).toContain('网络')
    expect(text).not.toContain('没点完')
  })

  it('still names an unrecognised code rather than hiding it', () => {
    // 「未知原因」 alone loses the one clue anyone could act on.
    expect(explainAccountLinkFailure('brand_new_code')).toContain('brand_new_code')
  })

  it('handles a missing reason', () => {
    expect(explainAccountLinkFailure(undefined)).toContain('未知原因')
  })
})

describe('explainRepoInstallFailure', () => {
  it('turns every code the install callback can emit into a sentence', () => {
    // Read off github_install.py.
    for (const code of [
      'invalid_state',
      'missing_installation_id',
      'project_not_found',
      'no_accessible_repos',
      'forge_conflict',
      'github_error',
      'access_denied',
      'upstream_not_accessible',
      'repository_selection_required',
      'repository_write_required',
      'internal_error',
    ]) {
      expect(explainRepoInstallFailure(code)).not.toContain(code)
    }
  })

  it('names the project already holding the repo when the caller may see it', () => {
    const text = explainRepoInstallFailure('repository_taken', { repo: 'acme/widgets', holder: 'Widgets' })
    expect(text).toContain('acme/widgets')
    expect(text).toContain('「Widgets」')
    expect(text).not.toContain('repository_taken')
  })

  it('does not invent a holder it was not told about', () => {
    const text = explainRepoInstallFailure('repository_taken', { repo: 'acme/widgets' })
    expect(text).toContain('acme/widgets')
    expect(text).toContain('看不到')
  })

  it('is about the repo, not the account — the two flows have separate copy', () => {
    expect(explainRepoInstallFailure('invalid_state')).toContain('仓库')
    expect(explainAccountLinkFailure('invalid_state')).toContain('账号')
  })
})
