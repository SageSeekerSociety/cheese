// 连接 GitHub 账号 (#192 user-to-server link, github_account_link.py) — pure
// helpers kept out of the view so they're testable without mounting Vuetify.
import type { OAuthConnectionInfo } from '../cx_types'

import { t } from '@/i18n'

export const GITHUB_APP_PROVIDER_ID = 'github_app'

// The list endpoint returns every provider a user has linked; this picks the
// one this section cares about.
export function findGithubAccountConnection(connections: OAuthConnectionInfo[]): OAuthConnectionInfo | null {
  return connections.find((c) => c.providerId === GITHUB_APP_PROVIDER_ID) ?? null
}

// The callback routes hand their outcome back as `?reason=<code>` — an
// identifier meant for us, not a sentence meant for a user. The page used to
// splice the code straight into the text, so a real person got 「连接账号失败：
// already_linked」 (#222, 2026-08-10 incident). Worse, that code is the ONLY
// feedback anyone gets, and it lands in whichever browser followed the link —
// which may not be the one that started the flow. So each code says what
// happened AND what to do about it; an unknown code still names itself, since
// a code we can't explain is more useful than 「原因未知」.
//
// Sources: `github_account_link.py` (account) and `github_install.py` (repo).
const ACCOUNT_LINK_REASONS = ['already_linked', 'oauth_failed', 'github_unreachable', 'invalid_state']

const REPO_INSTALL_REASONS = [
  'invalid_state',
  'missing_installation_id',
  'project_not_found',
  'no_accessible_repos',
  'forge_conflict',
  'github_error',
  'access_denied',
  'upstream_not_accessible',
  'repository_selection_required',
  'repository_write_required',
  'internal_error',
]

function explain(kind: 'account' | 'repo', known: string[], reason: string | undefined): string {
  if (!reason) return t('work.projectSettings.github.unknown')
  if (!known.includes(reason)) return t('work.projectSettings.github.unknownCode', { code: reason })
  return t(`work.projectSettings.github.${kind}.${reason}`)
}

export function explainAccountLinkFailure(reason: string | undefined): string {
  return t('work.projectSettings.github.accountFailed', { reason: explain('account', ACCOUNT_LINK_REASONS, reason) })
}

// `repository_taken` carries the repo and, when the person connecting may see
// it, the name of the project already holding it (`holder`).
export function explainRepoInstallFailure(
  reason: string | undefined,
  details: { repo?: string; holder?: string } = {}
): string {
  if (reason === 'repository_taken') {
    const reasonText = t('work.projectSettings.github.taken', {
      repo: details.repo ?? t('work.projectSettings.github.takenRepo'),
      where: details.holder
        ? t('work.projectSettings.github.takenHolder', { holder: details.holder })
        : t('work.projectSettings.github.takenHidden'),
      next: details.holder
        ? t('work.projectSettings.github.takenNextHolder')
        : t('work.projectSettings.github.takenNextHidden'),
    })
    return t('work.projectSettings.github.repoFailed', { reason: reasonText })
  }
  return t('work.projectSettings.github.repoFailed', { reason: explain('repo', REPO_INSTALL_REASONS, reason) })
}

// tokenExpires is null whenever the GitHub App wasn't configured to expire
// user tokens — that's "unknown/doesn't expire", never "expired". Only a
// timestamp that has actually passed counts.
export function isGithubAccountTokenExpired(conn: OAuthConnectionInfo, now: Date = new Date()): boolean {
  if (!conn.tokenExpires) return false
  const expires = new Date(conn.tokenExpires).getTime()
  if (Number.isNaN(expires)) return false
  return expires <= now.getTime()
}
