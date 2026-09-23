import { t } from '@/i18n'

// Provider names are brand names; only the Chinese brands differ by language.
const NAMES: Record<string, () => string> = {
  github: () => 'GitHub',
  google: () => 'Google',
  microsoft: () => 'Microsoft',
  qq: () => 'QQ',
  wechat: () => t('account.oauth.provider.wechat'),
  weibo: () => t('account.oauth.provider.weibo'),
}

export function oauthProviderName(id: string): string {
  return Object.hasOwn(NAMES, id) ? NAMES[id]() : id
}
