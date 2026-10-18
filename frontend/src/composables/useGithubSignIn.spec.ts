import { beforeEach, describe, expect, it, vi } from 'vitest'

import { signInWithGithub } from './useGithubSignIn'

import { UserAuthApi } from '@/api/userAuth'
import { goAuthorize } from '@/lib/desktopApp'

vi.mock('@/api/userAuth', () => ({
  UserAuthApi: {
    oauthLoginUrl: vi.fn(() => 'https://api.example.test/users/auth/oauth/login/github_app'),
    getGithubReauthIntent: vi.fn(),
  },
}))
vi.mock('@/lib/desktopApp', () => ({ goAuthorize: vi.fn() }))
vi.mock('vuetify-sonner', () => ({ toast: { error: vi.fn() } }))

describe('GitHub reauthentication', () => {
  beforeEach(() => vi.clearAllMocks())

  it('binds GitHub reauthorization to the account that opened it', async () => {
    vi.mocked(UserAuthApi.getGithubReauthIntent).mockResolvedValue({ intent: 'account-ticket' })

    await signInWithGithub()

    expect(goAuthorize).toHaveBeenCalledWith(
      'https://api.example.test/users/auth/oauth/login/github_app?intent=account-ticket'
    )
  })

  it('uses regular GitHub sign-in when this account has no existing authorization', async () => {
    vi.mocked(UserAuthApi.getGithubReauthIntent).mockResolvedValue({ intent: null })

    await signInWithGithub()

    expect(goAuthorize).toHaveBeenCalledWith('https://api.example.test/users/auth/oauth/login/github_app')
  })
})
