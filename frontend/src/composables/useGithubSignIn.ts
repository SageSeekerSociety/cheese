import { UserAuthApi } from '@/api/userAuth'
import { desktopCan, goAuthorize } from '@/lib/desktopApp'

/** Renew the same GitHub App connection used for login and repository access. */
export function signInWithGithub(): void {
  if (desktopCan('links')) {
    goAuthorize(UserAuthApi.oauthLoginUrl('github_app'))
    return
  }
  UserAuthApi.redirectToOAuthLogin('github_app')
}
