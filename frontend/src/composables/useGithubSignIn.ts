import { toast } from 'vuetify-sonner'

import { UserAuthApi } from '@/api/userAuth'
import { t } from '@/i18n'
import { goAuthorize } from '@/lib/desktopApp'

/** Renew the same GitHub App connection used for login and repository access. */
export async function signInWithGithub(): Promise<void> {
  try {
    const { intent } = await UserAuthApi.getGithubReauthIntent()
    const url = UserAuthApi.oauthLoginUrl('github_app')
    goAuthorize(intent ? `${url}?intent=${encodeURIComponent(intent)}` : url)
  } catch {
    toast.error(t('account.oauth.error.startFailed'))
  }
}
