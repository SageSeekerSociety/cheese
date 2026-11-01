import { UserAuthApi } from '@/api/userAuth'
import { t } from '@/i18n'
import { goAuthorize } from '@/lib/desktopApp'
import { toast } from '@/lib/toast'

/** Connect GitHub to the signed-in account, or renew the connection it has:
 *  the same GitHub App authorization used for login and repository access.
 *  The browser comes back to this page, or the app shows it there. */
export async function signInWithGithub(): Promise<void> {
  try {
    const { intent } = await UserAuthApi.getGithubAppIntent(window.location.pathname)
    goAuthorize(`${UserAuthApi.oauthLoginUrl('github_app')}?intent=${encodeURIComponent(intent)}`)
  } catch {
    toast.error(t('account.oauth.error.startFailed'))
  }
}
