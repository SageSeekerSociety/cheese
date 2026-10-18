import { beforeEach, describe, expect, it, vi } from 'vitest'

import { UserAuthApi } from '@/api/userAuth'
import { desktopCan, goAuthorize } from '@/lib/desktopApp'

import { signInWithGithub } from './useGithubSignIn'

vi.mock('@/api/userAuth', () => ({
  UserAuthApi: {
    oauthLoginUrl: vi.fn(() => 'https://api.example.test/users/auth/oauth/login/github_app'),
    redirectToOAuthLogin: vi.fn(),
  },
}))
vi.mock('@/lib/desktopApp', () => ({ desktopCan: vi.fn(), goAuthorize: vi.fn() }))

describe('GitHub reauthentication', () => {
  beforeEach(() => vi.clearAllMocks())

  it('opens GitHub sign-in in the system browser from the desktop app', () => {
    vi.mocked(desktopCan).mockReturnValue(true)

    signInWithGithub()

    expect(goAuthorize).toHaveBeenCalledWith('https://api.example.test/users/auth/oauth/login/github_app')
    expect(UserAuthApi.redirectToOAuthLogin).not.toHaveBeenCalled()
  })

  it('navigates to GitHub sign-in in a browser', () => {
    vi.mocked(desktopCan).mockReturnValue(false)

    signInWithGithub()

    expect(UserAuthApi.redirectToOAuthLogin).toHaveBeenCalledWith('github_app')
  })
})
