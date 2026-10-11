/**
 * 预览站「题目板拆出来的那半张画面」这一组的第二批：`views/spaces/detail/` 里新拆出来的
 * 画面（`XView.vue` —— 只收 props、只发事件）。和 `catalogScenesSpaces.ts` 同一组，只是
 * 条目多了、`catalog.ts` 又顶到近一千行的上限，所以另起一份分册。规矩见 `catalog.ts`，
 * 数据见 `catalogScenesSpacesDetailFixtures.ts`。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import { announcement, memberRow } from './catalogScenesSpacesDetailFixtures'

import AnnouncementsView from '@/views/spaces/detail/AnnouncementsView.vue'
import MembersView from '@/views/spaces/detail/MembersView.vue'

/** 画面本来要装 Vuetify 和一套语言包。 */
const UI: CatalogNeed[] = ['vuetify', 'i18n']

/** 成员页的画面里「邀请去哪」交给路由解析，所以场景里也把路由装上，落点是空白的通配页。 */
const UI_TR: CatalogNeed[] = [...UI, 'router']

export const SCENES_SPACES_DETAIL_ENTRIES: CatalogEntry[] = [
  {
    id: 'announcements-view',
    title: 'AnnouncementsView',
    about:
      '「空间公告」那一页的画面：公告卡片那一列、收在「已到期」那一折里的过期几条、发布/编辑那个对话框。发、改、删、置顶都 emit 给容器 `Announcements.vue`；只有管理员看得到那些操作。',
    file: 'src/views/spaces/detail/AnnouncementsView.vue',
    component: AnnouncementsView,
    needs: UI,
    args: {
      current: [],
      expired: [],
      loaded: true,
      isManager: false,
      submitting: false,
      notifyCount: 0,
      failed: false,
      errorReason: null,
      forbidden: false,
      open: false,
    },
    states: [
      {
        name: '一条公告都没有',
        note: '读回来是空的：就地说一句「暂无公告」，不是一块空白。',
        props: {},
        expect: '暂无公告',
      },
      {
        name: '列着公告',
        note: '管理员看得到发布那颗按钮，卡片上带置顶/编辑/删除那几个动作。',
        props: { current: [announcement(), announcement({ id: 2, title: '考试提醒', pinned: true })], isManager: true },
        expect: '学期安排',
      },
      {
        name: '读失败',
        note: '一条都没读到、而且是读失败了：换成错误那一块。读到过的话不会走这里。',
        props: { failed: true, errorReason: '加载公告失败' },
        expect: '加载公告失败',
      },
    ],
  },
  {
    id: 'members-view',
    title: 'MembersView',
    about:
      '「成员与角色」那一页的画面：页头（邀请入口指向设置里那两页）、搜索框、那张成员表（名字、角色的一句话说明、加入方式），改角色的按钮按所有者 / 管理员 / 成员分档。读名单、改角色、确认框都在容器 `Members.vue` 里。',
    file: 'src/views/spaces/detail/MembersView.vue',
    component: MembersView,
    needs: UI_TR,
    args: {
      spaceId: 8,
      rows: [],
      isOwner: true,
      busy: false,
      failed: false,
      errorDetail: null,
    },
    states: [
      {
        name: '一个成员都没有',
        note: '名单空着：就地说一句「暂无成员」，不是一块空白。',
        props: {},
        expect: '暂无成员',
      },
      {
        name: '列着成员',
        note: '一行普通成员（写着用过的码）：所有者才看得到「设为管理员」那颗按钮。',
        props: {
          rows: [memberRow(), memberRow({ userId: 12, handle: 'bo', name: '波波', role: 'ADMIN', viaCode: null })],
        },
        expect: '共 2 人',
      },
      {
        name: '读失败',
        note: '名单没读出来：错误替换整个列表区，不落进「暂无成员」—— 那会让失败看着像本来就没人。',
        props: { failed: true, errorDetail: '加载成员失败' },
        expect: '加载成员失败',
      },
    ],
  },
]
