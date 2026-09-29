import { t } from '@/i18n'

/** 个人设置的几块。桌面上是左边那条侧栏，手机上是内容上方的一排标签，同一份清单。 */
export const SETTINGS_SECTIONS = [
  { label: () => t('account.profile.title'), route: { name: 'UserSettingsProfile' }, icon: 'mdi-account' },
  { label: () => t('account.settings.realName'), route: { name: 'UserSettingsRealName' }, icon: 'mdi-account-card' },
  { label: () => t('account.security.title'), route: { name: 'UserSettingsSecurity' }, icon: 'mdi-lock' },
  { label: () => t('account.settings.install'), route: { name: 'UserSettingsApp' }, icon: 'mdi-cellphone-arrow-down' },
] as const
