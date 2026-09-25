import { t } from '@/i18n'

// A passkey belongs to one site (its RP ID). On an address that site does not
// cover — another domain serving the same app — the browser refuses before it
// asks the person anything, and the only useful thing to say is where passkeys
// do work. `rpId` is the site the server asked for.
export function passkeyWrongHostMessage(error: unknown, rpId: string | undefined): string | null {
  const e = error as { name?: string; code?: string } | null
  if (!rpId || (e?.code !== 'ERROR_INVALID_RP_ID' && e?.name !== 'SecurityError')) return null
  return t('account.passkeyWrongHost', { host: rpId })
}
