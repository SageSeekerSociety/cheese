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
      components: {
        default: () => import('@/layouts/user/Settings.vue'),
        sidebar: () => import('@/views/user/settings/SettingsSidebar.vue'),
      },
      redirect: { name: 'UserSettingsProfile' },
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
          // 「安装到手机」：装到主屏幕的说明 + 一次 beforeinstallprompt 机会。
          // 路径叫 app 而不是 install，是因为将来这一页还会放别的客户端形态
          // （桌面端安装、版本信息）——它答的是「芝士在哪些设备上是应用」。
          path: 'app',
          name: 'UserSettingsApp',
          component: () => import('@/views/user/settings/Install.vue'),
        },
      ],
    },
    {
      path: ':id',
      name: 'UserDetail',
      component: () => import('@/layouts/user/UserDetail.vue'),
      children: [
        {
          path: '',
          name: 'UserDefault',
          redirect: { name: 'UserFollowing' },
        },
        {
          path: 'following',
          name: 'UserFollowing',
          component: () => import('@/views/user/Following.vue'),
        },
        {
          path: 'follower',
          name: 'UserFollower',
          component: () => import('@/views/user/Follower.vue'),
        },
      ],
    },
  ],
} as RouteRecordRaw
