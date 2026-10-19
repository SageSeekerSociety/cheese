import { toast } from 'vuetify-sonner'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { signInWithGithub } from './useGithubSignIn'

import { UserAuthApi } from '@/api/userAuth'
import { goAuthorize } from '@/lib/desktopApp'

vi.mock('@/api/userAuth', () => ({
  UserAuthApi: {
    oauthLoginUrl: vi.fn(() => 'https://api.example.test/users/auth/oauth/login/github_app'),
    getGithubAppIntent: vi.fn(),
  },
}))
vi.mock('@/lib/desktopApp', () => ({ goAuthorize: vi.fn() }))
vi.mock('vuetify-sonner', () => ({ toast: { error: vi.fn() } }))

describe('GitHub authorization from the app', () => {
  beforeEach(() => vi.clearAllMocks())

  it('ties the authorization to the account that opened it', async () => {
    vi.mocked(UserAuthApi.getGithubAppIntent).mockResolvedValue({ intent: 'account-ticket' })

    await signInWithGithub()

    expect(goAuthorize).toHaveBeenCalledWith(
      'https://api.example.test/users/auth/oauth/login/github_app?intent=account-ticket'
    )
  })

  it('never falls back to a sign-in that is not tied to the account', async () => {
    vi.mocked(UserAuthApi.getGithubAppIntent).mockRejectedValue(new Error('offline'))

    await signInWithGithub()

    expect(goAuthorize).not.toHaveBeenCalled()
    expect(toast.error).toHaveBeenCalled()
  })
})
