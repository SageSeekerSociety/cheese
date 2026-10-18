/**
 * 通用基础件那一册：句子里提到的一个人（`UserRef`）、产品里唯一那颗「点了去哪」
 * 的字（`NavLink`）。
 *
 * 从 `../catalog.ts` 的 `CATALOG` 里拆出来的一册：条目本身一个字没改，只是搬了个
 * 地方。原先它们内联在那个文件里，和下面几百行别的场景挤在一起。
 */
import type { CatalogEntry } from './types'

import NavLink from '@/components/common/NavLink.vue'
import UserRef from '@/components/common/UserRef.vue'

export const COMMON_ENTRIES: CatalogEntry[] = [
  {
    id: 'user-ref',
    title: 'UserRef',
    about:
      '句子里提到的一个人：画成名字引用，不带 @、也不给底（带 @ 的实心 chip 只留给真的通知到人的那一处）。给了去处才可点。',
    file: 'src/components/common/UserRef.vue',
    component: UserRef,
    needs: [],
    states: [
      {
        name: '有去处',
        note: '给了 to 才是人可以点的名字：进 Tab 顺序，悬停加深底色，按一下 emit navigate。',
        props: { handle: 'alice', name: '爱丽丝', to: { path: '/projects/p1/members/alice' } },
        expect: '爱丽丝',
      },
      {
        name: '只知道名字',
        note: '没有 handle 就没有去处（找不到那个人的主页）：照样画名字，但不可点。',
        props: { name: '新来的同学' },
        expect: '新来的同学',
      },
      {
        name: '长名字',
        note: '名字长到换行也照常折行，不缩字号、不截断。',
        props: {
          handle: 'someone-with-a-long-handle',
          name: '一位名字很长的同学（校外）',
          to: { path: '/projects/p1/members/x' },
        },
        expect: '一位名字很长的同学（校外）',
      },
    ],
  },
  {
    id: 'nav-link',
    title: 'NavLink',
    about: '产品里唯一「点了去哪」的那颗字：有路由是一条链接，没有路由是一行静态的字。',
    file: 'src/components/common/NavLink.vue',
    component: NavLink,
    needs: ['router'],
    states: [
      {
        name: '有路由',
        note: '装了路由：画成 <a href>，点一下 push（Cmd/中键点还是新开一页，浏览器说了算）。',
        props: { to: { path: '/projects/p1/settings' } },
        slot: '项目设置',
        expect: '项目设置',
      },
      {
        name: '没有路由',
        note: '不装路由：一样画一个字，但没有 href、进不了 Tab 顺序 —— 同一个组件，两种环境都挂得起来。',
        props: { to: { path: '/projects/p1/settings' } },
        slot: '项目设置',
        needs: [],
        expect: '项目设置',
      },
      {
        name: '去不成的去处',
        note: '目标的名字在、必需参数不在（`resolve` 会抛）：这里什么都不画成链接 —— 不是死代码，是真会走到的退路。',
        props: { to: { name: 'workspace-overview', params: {} } },
        slot: '去看看',
        expect: '去看看',
      },
    ],
  },
]
