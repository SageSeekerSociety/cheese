import { t } from '@/i18n'
import { desktopCan } from '@/lib/desktopApp'

/** 个人设置的几块。桌面上是左边那条侧栏，手机上是内容上方的一排标签，同一份清单。
 *  「通用」只在桌面 app 里有：那里是这台电脑上的 app 自己的设置。 */
export const SETTINGS_SECTIONS = [
  { label: () => t('account.profile.title'), route: { name: 'UserSettingsProfile' }, icon: 'mdi-account' },
  { label: () => t('account.settings.realName'), route: { name: 'UserSettingsRealName' }, icon: 'mdi-account-card' },
  { label: () => t('account.security.title'), route: { name: 'UserSettingsSecurity' }, icon: 'mdi-lock' },
  ...(desktopCan('autostart')
    ? [{ label: () => t('account.settings.general'), route: { name: 'UserSettingsGeneral' }, icon: 'mdi-tune-variant' }]
    : []),
  { label: () => t('account.settings.install'), route: { name: 'UserSettingsApp' }, icon: 'mdi-cellphone-arrow-down' },
]
