import { t } from '@/i18n'

// The sign-in page shows a notice only for these keys. Its text comes from the
// catalog, never from the URL: a link anyone can write must not be able to put
// its own sentence on this page.
const NOTICES: Record<string, () => string> = {
  passwordReset: () => t('account.signIn.notice.passwordReset'),
}

export type SignInNoticeKey = 'passwordReset'

export function signInNotice(key: unknown): string | null {
  return typeof key === 'string' && Object.hasOwn(NOTICES, key) ? NOTICES[key]() : null
}
