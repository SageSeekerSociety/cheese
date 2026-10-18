import { toast } from 'vuetify-sonner'

import { UserAuthApi } from '@/api/userAuth'
import { t } from '@/i18n'
import { goAuthorize } from '@/lib/desktopApp'

/** Connect GitHub to the signed-in account, or renew the connection it has:
 *  the same GitHub App authorization used for login and repository access. */
export async function signInWithGithub(): Promise<void> {
  try {
    const { intent } = await UserAuthApi.getGithubAppIntent()
    goAuthorize(`${UserAuthApi.oauthLoginUrl('github_app')}?intent=${encodeURIComponent(intent)}`)
  } catch {
    toast.error(t('account.oauth.error.startFailed'))
  }
}
