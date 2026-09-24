import { t } from '@/i18n'

// Provider names are brand names; only the Chinese brands differ by language.
const NAMES: Record<string, () => string> = {
  github: () => 'GitHub',
  google: () => 'Google',
  microsoft: () => 'Microsoft',
  qq: () => 'QQ',
  ruc: () => t('account.oauth.provider.ruc'),
  wechat: () => t('account.oauth.provider.wechat'),
  weibo: () => t('account.oauth.provider.weibo'),
}

export function oauthProviderName(id: string): string {
  return Object.hasOwn(NAMES, id) ? NAMES[id]() : id
}

const ICONS: Record<string, string> = {
  github: 'mdi-github',
  google: 'mdi-google',
  microsoft: 'mdi-microsoft',
  qq: 'mdi-qqchat',
  ruc: 'mdi-school-outline',
  wechat: 'mdi-wechat',
  weibo: 'mdi-sina-weibo',
}

export function oauthProviderIcon(id: string): string {
  return Object.hasOwn(ICONS, id) ? ICONS[id] : 'mdi-account-circle'
}
