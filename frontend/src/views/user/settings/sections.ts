import type { SettingsGroup } from '@/components/common/SettingsOverlay.vue'

import { t } from '@/i18n'
import { desktopCan } from '@/lib/desktopApp'

/** 个人设置的目录：桌面上是浮层左边那一列，手机上是打开设置先看到的那一页。
 *  设备、模型和额度是这个人名下那个单人团队的，和团队设置里的那几栏是同一份页面。
 *  「这台设备」和「通用」只在桌面 app 里有：说的是 app 所在的这台电脑。 */
export function settingsGroups(): SettingsGroup[] {
  const item = (name: string, label: string, icon: string) => ({ key: name, label, icon, to: { name } })
  return [
    {
      key: 'account',
      title: t('account.settings.groups.account'),
      items: [
        item('UserSettingsProfile', t('account.profile.title'), 'mdi-account'),
        item('UserSettingsRealName', t('account.settings.realName'), 'mdi-account-card'),
        item('UserSettingsSecurity', t('account.security.title'), 'mdi-lock'),
        item('UserSettingsNotifications', t('account.settings.notifications'), 'mdi-bell-outline'),
        item('UserSettingsAssistant', t('account.settings.assistant'), 'mdi-shield-check-outline'),
        item('UserSettingsInvites', t('account.settings.invites'), 'mdi-account-plus-outline'),
      ],
    },
    {
      key: 'devices',
      title: t('account.settings.groups.devices'),
      items: [
        item('UserSettingsConnections', t('account.settings.connections'), 'mdi-link-variant'),
        item('UserSettingsCompute', t('home.nav.teamCompute'), 'mdi-laptop'),
        item('UserSettingsModels', t('home.nav.teamModels'), 'mdi-creation-outline'),
      ],
    },
    {
      key: 'usage',
      title: t('account.settings.groups.usage'),
      items: [item('UserSettingsCredits', t('home.nav.teamCredits'), 'mdi-wallet-outline')],
    },
    ...(desktopCan('autostart') || desktopCan('device')
      ? [
          {
            key: 'app',
            title: t('account.settings.groups.app'),
            items: [
              ...(desktopCan('device')
                ? [item('UserSettingsThisDevice', t('account.thisDevice.title'), 'mdi-laptop-account')]
                : []),
              ...(desktopCan('autostart')
                ? [item('UserSettingsGeneral', t('account.settings.general'), 'mdi-tune-variant')]
                : []),
            ],
          },
        ]
      : []),
  ]
}
