import type { RouteRecordRaw } from 'vue-router'

import RouterPassThrough from '@/layouts/RouterPassThrough.vue'

export default {
  path: '/users',
  name: 'User',
  component: RouterPassThrough,
  children: [
    {
      path: 'settings',
      name: 'UserSettings',
      // 盖在整个窗口上的一层（layouts/user/Settings.vue）。这一条自己的地址在桌面上
      // 落到「个人资料」，手机上是目录。
      component: () => import('@/layouts/user/Settings.vue'),
      meta: {
        titleKey: 'navigation.userMenu.settings',
        palette: { label: 'navigation.userMenu.settings', icon: 'mdi-account-cog-outline' },
        settingsOverlay: true,
        hideTabs: true,
      },
      children: [
        {
          path: 'profile',
          name: 'UserSettingsProfile',
          component: () => import('@/views/user/settings/Profile.vue'),
        },
        {
          path: 'security',
          name: 'UserSettingsSecurity',
          component: () => import('@/views/user/settings/Security.vue'),
        },
        {
          // 芝士额度：自己本月用了多少，按协作、问答、写作分。
          path: 'usage',
          name: 'UserSettingsUsage',
          component: () => import('@/views/user/settings/Usage.vue'),
        },
        {
          path: 'realname',
          name: 'UserSettingsRealName',
          component: () => import('@/views/user/settings/RealName.vue'),
        },
        {
          // 通知：哪一类事件走哪个渠道、安静时段、摘要频率。改一项存一项。
          path: 'notifications',
          name: 'UserSettingsNotifications',
          component: () => import('@/views/user/settings/Notifications.vue'),
        },
        {
          // 接入的电脑：这个人的工作电脑，哪个项目都能用。
          path: 'devices',
          name: 'UserSettingsDevices',
          component: () => import('@/views/MyDevicesView.vue'),
          meta: { palette: { label: 'account.settings.devices', icon: 'mdi-laptop' } },
        },
        {
          // 芝士替这个人用的邮箱和飞书。
          path: 'connections',
          name: 'UserSettingsConnections',
          component: () => import('@/views/MyConnectionsView.vue'),
          meta: { palette: { label: 'account.settings.connections', icon: 'mdi-link-variant' } },
        },
        {
          // 「通用」：这台电脑上的桌面 app 自己的设置。浏览器里没有这一块。
          path: 'general',
          name: 'UserSettingsGeneral',
          component: () => import('@/views/user/settings/General.vue'),
          beforeEnter: async () => {
            const { desktopCan } = await import('@/lib/desktopApp')
            return desktopCan('autostart') ? true : { name: 'UserSettingsProfile' }
          },
        },
      ],
    },
    {
      // 一个人的主页按 handle 找：@提及、成员名册、队友都是这么认人的。旁边的
      // settings 是静态段，vue-router 先认静态段；这个词也不许注册成用户名
      // （app.domain.identity.handles）。
      path: ':handle',
      name: 'UserPage',
      component: () => import('@/views/ProfileView.vue'),
      props: true,
    },
  ],
} as RouteRecordRaw
