/**
 * 组件预览站里「侧栏那一组」的条目。
 *
 * 这六件是 TopicSidebar 拆出来、只管画的那几件：一行话题、顶上那组置顶入口、项目名
 * 那一行、一个组头、一组已归档，外加那颗未读角标。它们和别的条目没有两样，单独放一
 * 份只是因为 `frontend/src` 下的文件有一千行的上限，而 `catalog.ts` 是一份会一直长
 * 下去的注册表——这一组自己的三百多行搬出来，两边都离上限远一点。条目的规矩（这是什
 * 么 / 在哪儿 / 需要哪几样 / 看哪几格）见 `catalog.ts`；数据见 `catalogFixtures.ts`。
 *
 * 这里的 `CatalogEntry` 是 type-only 引用：`catalog.ts` 反过来要 `RAIL_ENTRIES` 这个
 * 值，运行时不构成循环。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import { RAIL_ACTIONS, RAIL_MARKS, RAIL_PAGES, RAIL_ROOT_TOPIC, RAIL_ROWS, RAIL_TERMS } from './catalogFixtures'

import TopicRailBadge from '@/components/topic-sidebar/TopicRailBadge.vue'
import TopicRailHeader from '@/components/topic-sidebar/TopicRailHeader.vue'
import TopicRailPinnedRows from '@/components/topic-sidebar/TopicRailPinnedRows.vue'
import TopicRailRootRow from '@/components/topic-sidebar/TopicRailRootRow.vue'
import TopicRailRow from '@/components/topic-sidebar/TopicRailRow.vue'

const UI: CatalogNeed[] = ['vuetify']

/** 一行话题那些不变的 props：每一格换的只是 `row` 和它那几个开关。 */
function rowProps(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    selected: false,
    page: false,
    renaming: false,
    menuOpen: false,
    stalled: false,
    toggleTitle: '收起',
    actions: () => RAIL_ACTIONS,
    ...over,
  }
}

// ---- 项目侧栏：TopicSidebar 拆出来的那一组「只管画」的件 --------------------
//
// 一行话题、顶上那组置顶入口、项目名那一行、一个组头、一组已归档，外加那颗未读
// 角标。它们一件都不认识路由、也不取数——每一格的 `needs` 就是这句话的机械证明：
// 角标一件都不用装，话题行只要 Vuetify，要读词表的那两件才多一个 i18n。
export const RAIL_ENTRIES: CatalogEntry[] = [
  {
    id: 'topic-rail-badge',
    title: 'TopicRailBadge',
    about: '未读角标：一颗裸的琥珀数字，或者一个纯点。侧栏上六处未读共用它。',
    file: 'src/components/topic-sidebar/TopicRailBadge.vue',
    component: TopicRailBadge,
    // 一条依赖都不装：它只把一个数字画成「99+」，`countLabel` 是纯函数。
    needs: [],
    states: [
      {
        name: '几条新消息',
        note: '行尾唯一常驻的右对齐元素：裸的琥珀数字，不画成药丸也不画成红圆。',
        props: { count: 3 },
        expect: '3',
      },
      {
        name: '多到写不下',
        note: '超过 99 就写 99+：一个失控的计数不该把行撑开。',
        props: { count: 128 },
        expect: '99+',
      },
      {
        name: '只给一个点',
        note: '收起来的一组：别人话题里有几条与我无关，但「那边有动静」值得知道。',
        props: { dot: true, title: '其他话题里有新消息' },
      },
      {
        name: '含收起来的子话题',
        note: '折叠不能把「有新消息」吞掉：这句 title 说的就是右边那个数是怎么来的。',
        props: { count: 7, title: '含收起的子话题 4 条新消息' },
        expect: '7',
      },
    ],
  },
  {
    id: 'topic-rail-row',
    title: 'TopicRailRow',
    about: '话题列表里的一行：左边一个 16px 槽，右边在忙的成员、未读和 hover 才浮现的 ⋯。',
    file: 'src/components/topic-sidebar/TopicRailRow.vue',
    component: TopicRailRow,
    // 一行不认识路由、也不取数：状态、折叠、未读全在 props 里（`row` 是
    // `lib/topicTree.ts` 的 `VisibleRow`，聚合已经在那边算完了）。
    needs: UI,
    states: [
      {
        name: '有队友在干活',
        note: '右边是那位队友的小头像，带一颗绿点。房间自己没有「在跑」这种状态，在干活的是成员。',
        props: rowProps({ row: RAIL_ROWS.working, marks: RAIL_MARKS.working }),
        expect: '写第 4 章的教案',
      },
      {
        name: '等你拍板',
        note: '琥珀点：有待处理的事项。未读的 @ 不点这颗灯（芝士汇报、递卡都 @人），未读有右边的数字。',
        props: rowProps({ row: RAIL_ROWS.awaits }),
        expect: '决定这学期用哪本教材',
      },
      {
        name: '一位成员卡住了',
        note: '房间在等的那位成员，小头像带红点和一圈淡红晕。判据（报错 / 等太久）是父级带钟算的，hover 说出是谁、为什么。',
        props: rowProps({ row: RAIL_ROWS.stalled, stalled: true, marks: RAIL_MARKS.stalled }),
        expect: '把成绩单导出成 CSV',
      },
      {
        name: '收起来的父话题',
        note: '折叠开关顶掉状态点，所以它自己带聚合色（橙 = 里面有事等你）；右边是聚上来的未读，说的是「这里还有内容」。',
        props: rowProps({ row: RAIL_ROWS.collapsed, toggleTitle: '展开：里面有待处理的事项' }),
        expect: '期末复习',
      },
      {
        name: '子话题',
        note: '缩进一级，左边一条竖向引导线；未读角标说的是这一行自己有几条。',
        props: rowProps({ row: RAIL_ROWS.sub }),
        expect: '第 3 题：为什么天空是蓝的',
      },
      {
        name: '整页形态（手机）',
        note: '行更高（44px），没有那颗 hover 才出现的 ⋯ —— 那里长按一行打开同一组操作（见 TopicSidebar.longPress）。',
        props: rowProps({ row: RAIL_ROWS.working, marks: RAIL_MARKS.working, page: true, selected: true }),
        expect: '写第 4 章的教案',
      },
    ],
  },
  {
    id: 'topic-rail-pinned',
    title: 'TopicRailPinnedRows',
    about: '项目名下面那两行：总览和资料库，和频道行同一种行。别的页进项目名菜单，这里不再加。',
    file: 'src/components/topic-sidebar/TopicRailPinnedRows.vue',
    component: TopicRailPinnedRows,
    // 只读词表和 props：哪几页露出来了、当前在哪一页，都是父级算好递进来的。
    needs: ['vuetify', 'i18n'],
    states: [
      {
        name: '站在资料库上',
        note: '选中态和频道行同一套。',
        props: {
          pages: RAIL_PAGES,
          routeName: 'project-library',
          terms: RAIL_TERMS,
          page: false,
        },
        expect: '资料库',
      },
      {
        name: '手机上：这一行收进项目菜单',
        note: '整页形态里这两页在点项目名弹出的面板里，这里不画。',
        props: {
          pages: RAIL_PAGES,
          routeName: 'workspace-overview',
          terms: RAIL_TERMS,
          page: true,
        },
      },
    ],
  },
  {
    id: 'topic-rail-root',
    title: 'TopicRailRootRow',
    about: '频道分组的第一行：项目自带的频道「综合」，固定在最上面。',
    file: 'src/components/topic-sidebar/TopicRailRootRow.vue',
    component: TopicRailRootRow,
    needs: ['vuetify', 'i18n'],
    states: [
      {
        name: '站在综合里',
        note: '选中态和未读角标都和频道行同一套。',
        props: {
          rootTopic: RAIL_ROOT_TOPIC,
          selectedTopicId: 't-root',
          page: false,
          unreadOf: (id: string) => (id === 't-root' ? 3 : 0),
        },
        expect: '综合',
      },
    ],
  },
  {
    id: 'topic-rail-header',
    title: 'TopicRailHeader',
    about: '项目名那一行：点名字弹出项目菜单（总览、资料库之外的几页、项目文档、设置），右边是搜索。',
    file: 'src/components/topic-sidebar/TopicRailHeader.vue',
    component: TopicRailHeader,
    // 名字连着 ⌄ 是一个按钮，点下去是菜单。
    // 整页形态下这一行填进顶栏（Teleport），另外两个形态里它就在原地。
    needs: ['vuetify', 'i18n'],
    states: [
      {
        name: '所有者',
        note: '菜单里是项目文档、总览和资料库之外的几页、项目设置和「转让项目」；成员上挂着私聊未读。',
        props: {
          page: false,
          column: false,
          projectName: '课程项目',
          privateUnreadTotal: 3,
          searchTitle: '搜索（⌘K）',
          menuOpen: false,
          docsActive: false,
          menuPages: [
            { key: 'project-members', label: 'navigation.project.members', icon: 'mdi-account-group-outline' },
            { key: 'project-routines', label: 'navigation.project.routines', icon: 'mdi-timer-cog-outline' },
          ],
          routeName: 'workspace-overview',
          terms: RAIL_TERMS,
          projectSelected: true,
          canTransfer: true,
          canLeave: false,
        },
        expect: '课程项目',
      },
      {
        name: '我不是所有者',
        note: '所有者换「转让项目」，其余的人换「退出项目」——两句是同一件事的两半。',
        props: {
          page: false,
          column: false,
          projectName: '别人的项目',
          privateUnreadTotal: 0,
          searchTitle: '搜索（⌘K）',
          menuOpen: false,
          docsActive: false,
          menuPages: [],
          routeName: 'project-members',
          terms: RAIL_TERMS,
          projectSelected: true,
          canTransfer: false,
          canLeave: true,
        },
        expect: '别人的项目',
      },
      {
        name: '还没选项目',
        note: '项目名退回一句「选择项目」，菜单里那几项点不动（`disabled` 而不是藏起来：不让人猜是不是没画）。',
        props: {
          page: false,
          column: false,
          projectName: '选择项目',
          privateUnreadTotal: 0,
          searchTitle: '搜索（⌘K）',
          menuOpen: false,
          docsActive: false,
          menuPages: [],
          routeName: null,
          terms: RAIL_TERMS,
          projectSelected: false,
          canTransfer: false,
          canLeave: false,
        },
        expect: '选择项目',
      },
      {
        name: '两栏（平板）',
        note: '这一行留在左栏自己的顶上（不填进顶栏），高度和底线照桌面那一条，和右边的顶栏接成一条线。',
        props: {
          page: true,
          column: true,
          projectName: '课程项目',
          privateUnreadTotal: 0,
          searchTitle: '搜索',
          menuOpen: false,
          docsActive: false,
          menuPages: [],
          routeName: 'project-routines',
          terms: RAIL_TERMS,
          projectSelected: true,
          canTransfer: false,
          canLeave: true,
        },
        expect: '课程项目',
      },
    ],
  },
]
