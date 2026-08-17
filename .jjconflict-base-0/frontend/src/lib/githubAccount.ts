// 连接 GitHub 账号 (#192 user-to-server link, github_account_link.py) — pure
// helpers kept out of the view so they're testable without mounting Vuetify.
import type { OAuthConnectionInfo } from '../cx_types'

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
// a code we can't explain is more useful than 「未知原因」.
//
// Sources: `github_account_link.py` (account) and `github_install.py` (repo).
const ACCOUNT_LINK_REASONS: Record<string, string> = {
  already_linked: '这个 GitHub 账号已经绑在另一个平台账号上了。换一个 GitHub 账号，或者让对方先断开。',
  oauth_failed: 'GitHub 授权没走完（换 token 失败）。多半是授权页没点完或网络中断，重试一次。',
  invalid_state: '授权链接已失效或被用过了。请回到本页重新点「连接 GitHub 账号」，不要复用旧链接。',
}

const REPO_INSTALL_REASONS: Record<string, string> = {
  invalid_state: '安装链接已失效或被用过了。请回到本页重新点一次「连接 GitHub 仓库」。',
  missing_installation_id: 'GitHub 没有回传安装 ID，这次安装没有生效。重试一次。',
  project_not_found: '找不到这个项目，可能刚被删除。',
  no_accessible_repos: '这次安装没有授权任何仓库。请在 GitHub 的安装页里勾选至少一个仓库。',
  installation_conflict: '这个安装已经绑给别的项目了。请换一个仓库，或先在那边解绑。',
  github_error: 'GitHub 那边返回了错误。稍后重试；一直失败就看 GitHub 的状态页。',
  internal_error: '平台内部出错，这次连接没有生效。重试一次；仍失败请提 issue。',
}

function explain(table: Record<string, string>, reason: string | undefined): string {
  if (!reason) return '未知原因。'
  return table[reason] ?? `未知原因（${reason}）。`
}

export function explainAccountLinkFailure(reason: string | undefined): string {
  return `连接 GitHub 账号失败：${explain(ACCOUNT_LINK_REASONS, reason)}`
}

export function explainRepoInstallFailure(reason: string | undefined): string {
  return `连接 GitHub 仓库失败：${explain(REPO_INSTALL_REASONS, reason)}`
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
