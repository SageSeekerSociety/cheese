// 「连接 GitHub 账号」那一块的过期提示：只在这个账号真的要重新连接时出现。
// access token 过期本身不算 —— 后端拿续期 token 自己换新的（oauth/services/tokens.py），
// 这时说「授权已过期」是一句假话。没有能用的续期 token 时才提示。
import type { OAuthConnectionInfo } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest'

import GithubAccountSettings from './GithubAccountSettings.vue'

import { setLocale, t } from '@/i18n'

const PAST = '2020-01-01T00:00:00Z'

function conn(overrides: Partial<OAuthConnectionInfo>): OAuthConnectionInfo {
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

function shownText(c: OAuthConnectionInfo): string {
  const { container } = render(GithubAccountSettings, {
    props: { state: 'loaded', loadError: null, conn: c, connecting: false, disconnecting: false, notice: null },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  return container.textContent ?? ''
}

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
})

beforeEach(() => setLocale('zh-CN'))

afterEach(cleanup)

describe('the expired notice on 连接 GitHub 账号', () => {
  it('stays hidden when the access token has expired but a renewal token can replace it', () => {
    expect(shownText(conn({ tokenExpires: PAST, hasRefreshToken: true }))).not.toContain(
      t('work.projectSettings.githubAccount.expired')
    )
  })

  it('stays hidden for a link whose access token does not expire, renewal token or not', () => {
    expect(shownText(conn({ tokenExpires: null, hasRefreshToken: false }))).not.toContain(
      t('work.projectSettings.githubAccount.expired')
    )
  })

  it('shows when the access token has expired and nothing can renew it', () => {
    expect(shownText(conn({ tokenExpires: PAST, hasRefreshToken: false }))).toContain(
      t('work.projectSettings.githubAccount.expired')
    )
  })
})
