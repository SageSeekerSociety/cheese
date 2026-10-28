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
          // 芝士：哪些事一定先问、用自己的话补充的规矩，和最近替这个人做了什么。
          path: 'assistant',
          name: 'UserSettingsAssistant',
          component: () => import('@/views/user/settings/Assistant.vue'),
        },
        {
          // 邀请好友：自己的邀请码、还剩几个名额、邀请得了什么、邀请来的人走到了哪一步。
          path: 'invites',
          name: 'UserSettingsInvites',
          component: () => import('@/views/user/settings/Invites.vue'),
          meta: { palette: { label: 'account.settings.invites', icon: 'mdi-account-plus-outline' } },
        },
        {
          // 自己名下的设备、模型和额度：它们属于这个人名下那个单人团队，页面和团队设置里的
          // 是同一份（`views/teams/detail`），团队由这一层（layouts/user/Settings.vue）读好递下去。
          // 旧地址 `/teams/<自己>/compute` 这几条在 `teams/Detail.vue` 里转到这里。
          path: 'compute',
          name: 'UserSettingsCompute',
          component: () => import('@/views/teams/detail/Compute.vue'),
        },
        {
          path: 'models',
          name: 'UserSettingsModels',
          component: () => import('@/views/teams/detail/Models.vue'),
        },
        {
          path: 'credits',
          name: 'UserSettingsCredits',
          component: () => import('@/views/teams/detail/Credits.vue'),
        },
        {
          // 芝士替这个人用的邮箱和飞书。
          path: 'connections',
          name: 'UserSettingsConnections',
          component: () => import('@/views/MyConnectionsView.vue'),
          meta: { palette: { label: 'account.settings.connections', icon: 'mdi-link-variant' } },
        },
        {
          // 「这台设备」：桌面 app 所在的这台电脑作为一台设备，接入、名称、团队、Claude Code。
          // 浏览器里没有这一块。
          path: 'this-device',
          name: 'UserSettingsThisDevice',
          component: () => import('@/views/user/settings/ThisDevice.vue'),
          beforeEnter: async () => {
            const { desktopCan } = await import('@/lib/desktopApp')
            return desktopCan('device') ? true : { name: 'UserSettings' }
          },
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
