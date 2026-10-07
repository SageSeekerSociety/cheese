/**
 * 应用外壳上的两件在预览站里的条目：内测版本徽标（`VersionBadge`，`App.vue` 画它）和
 * 首页那一层的外框（`layouts/home/Home.vue`）。
 *
 * 规矩见 `catalog.ts`；单独一份是因为 `catalog.ts` 顶着一千行的上限。
 *
 * `Home.vue` 是目录里第一件布局：它自己没有内容，只决定子页面套不套 `.home-shell`，
 * 判据是当前路由的 `meta.publicLanding`。预览站给的是一个什么都不导航的内存路由
 * （`demoRouter`），落点是空白的通配页 —— 所以这里能看的就是「套了外框」那一种；
 * 「公开首页自己占满整屏」那一种要一条带 meta 的真路由，在 `layouts/home/Home.spec.ts`
 * 里测。
 *
 * 这里的 `CatalogEntry` 是 type-only 引用：`catalog.ts` 反过来要 `SHELL_ENTRIES` 这个
 * 值，运行时不构成循环。
 */
import type { AppVersion } from '@/api'
import type { CatalogEntry } from './catalog'

import VersionBadge from '@/components/common/VersionBadge.vue'
import Home from '@/layouts/home/Home.vue'

/** 一台开了内测徽标的机器上 `GET /api/version` 的回包。 */
const BUILD: AppVersion = {
  sha: '3f9c2ab71e4d05c88b6a2f1e9d7c3b40a5e6f812',
  short: '3f9c2ab',
  badge: true,
}

export const SHELL_ENTRIES: CatalogEntry[] = [
  {
    id: 'version-badge',
    title: 'VersionBadge',
    about: '内测版本徽标：钉在右上角的一颗小琥珀胶囊，写着后端此刻跑的是哪个提交；点一下复制完整的 sha。',
    file: 'src/components/common/VersionBadge.vue',
    component: VersionBadge,
    // 标题和「已复制」走 `t`（`@/i18n` 的全局实例，不用装插件）；没有 Vuetify 组件。
    needs: [],
    states: [
      {
        name: '有版本',
        note: '机器开了徽标（badge: true）：画短 sha，悬停看完整的那一串。徽标是 position: fixed，在这一页上它钉在窗口右上角，不在这一格里。',
        props: { version: BUILD },
        expect: '3f9c2ab',
      },
      {
        name: '机器没开徽标',
        note: '正式环境 badge 是 false：有版本也什么都不画。',
        props: { version: { ...BUILD, badge: false } },
      },
      {
        name: '还没问到（null）',
        note: '外壳还没拿到 /api/version 的回答，或者问失败了：什么都不画，不占位。',
        props: { version: null },
      },
    ],
  },
  {
    id: 'home-layout',
    title: 'Home',
    about: '首页那一层的外框：公开首页自己占满整屏，其余几页（待办、目录、团队与空间列表）套一层撑满的 .home-shell。',
    file: 'src/layouts/home/Home.vue',
    component: Home,
    // 两个 `<router-view>`：不装路由时 Vue 解析不了它，所以这一件离不开路由。
    needs: ['router'],
    states: [
      {
        name: '套着外框',
        note: '当前路由不是公开首页：子页面放进撑满的 .home-shell。预览站的路由落在空白页，所以框里是空的。',
        props: {},
        expectSelector: '.home-shell',
      },
    ],
  },
]
