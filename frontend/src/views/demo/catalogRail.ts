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

import TopicRailArchivedGroup from '@/components/topic-sidebar/TopicRailArchivedGroup.vue'
import TopicRailBadge from '@/components/topic-sidebar/TopicRailBadge.vue'
import TopicRailGroupToggle from '@/components/topic-sidebar/TopicRailGroupToggle.vue'
import TopicRailHeader from '@/components/topic-sidebar/TopicRailHeader.vue'
import TopicRailPinnedRows from '@/components/topic-sidebar/TopicRailPinnedRows.vue'
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
    about: '话题列表顶上那几行置顶入口：全局房间、这个项目露出来的几页、项目文档。',
    file: 'src/components/topic-sidebar/TopicRailPinnedRows.vue',
    component: TopicRailPinnedRows,
    // 只读词表和 props：哪几页露出来了、当前在哪一页，都是父级算好递进来的。
    needs: ['vuetify', 'i18n'],
    states: [
      {
        name: '置顶上那组入口',
        note: '和话题行同一种语法（同图标槽、同缩进、同选中态）：点它会发生什么，不用另学一遍。',
        props: {
          rootTopic: RAIL_ROOT_TOPIC,
          selectedTopicId: null,
          pages: RAIL_PAGES,
          routeName: 'project-library',
          terms: RAIL_TERMS,
          docsActive: false,
          docsOnRail: true,
          privateUnreadTotal: 3,
          page: false,
          unreadOf: (id: string) => (id === 't-root' ? 3 : 0),
        },
        expect: '资料库',
      },
      {
        name: '站在全局房间里',
        note: '选中态落在「全局」那一行上，它的未读角标也亮着——置顶行和话题行是同一套。',
        props: {
          rootTopic: RAIL_ROOT_TOPIC,
          selectedTopicId: 't-root',
          pages: RAIL_PAGES,
          routeName: 'workspace-running',
          terms: RAIL_TERMS,
          docsActive: false,
          docsOnRail: true,
          privateUnreadTotal: 0,
          page: false,
          unreadOf: () => 0,
        },
        expect: '全局',
      },
      {
        name: '打开的是项目文档',
        note: '四种文档（章程/决策/周报/记忆）在侧栏只占这一行，任何一种开着它都是选中态。',
        props: {
          rootTopic: RAIL_ROOT_TOPIC,
          selectedTopicId: null,
          pages: RAIL_PAGES,
          routeName: 'project-docs',
          terms: RAIL_TERMS,
          docsActive: true,
          docsOnRail: true,
          privateUnreadTotal: 0,
          page: false,
          unreadOf: () => 0,
        },
        expect: '项目文档',
      },
      {
        name: '手机上：列表只留话题',
        note: '那几页收进了项目名旁边那颗 ⌄（整页形态的列表只留话题），所以这里只剩「全局」一行。',
        props: {
          rootTopic: RAIL_ROOT_TOPIC,
          selectedTopicId: null,
          pages: RAIL_PAGES,
          routeName: 'workspace-running',
          terms: RAIL_TERMS,
          docsActive: false,
          docsOnRail: true,
          privateUnreadTotal: 0,
          page: true,
          unreadOf: () => 0,
        },
        expect: '全局',
      },
    ],
  },
  {
    id: 'topic-rail-header',
    title: 'TopicRailHeader',
    about: '项目名那一行：标识 + 搜索 + 项目菜单（那些一年点几次的页面）。',
    file: 'src/components/topic-sidebar/TopicRailHeader.vue',
    component: TopicRailHeader,
    // 名字和 chevron 是两个按钮：名字回项目首页，chevron 展开收起的那几页。
    // 整页形态下这一行填进顶栏（Teleport），另外两个形态里它就在原地。
    needs: ['vuetify', 'i18n'],
    states: [
      {
        name: '站在看板上',
        note: '首页是登录进项目看到的第一屏，所以它自己占着项目名那一行——菜单里不再列一遍。',
        props: {
          page: false,
          column: false,
          homeActive: true,
          homeKey: 'workspace-running',
          homeIcon: 'mdi-view-column-outline',
          projectName: '课程项目',
          privateUnreadTotal: 3,
          searchTitle: '搜索（⌘K）',
          menuOpen: false,
          menuPages: [{ key: 'project-routines', label: 'navigation.project.routines', icon: 'mdi-timer-cog-outline' }],
          routeName: 'workspace-running',
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
          homeActive: false,
          homeKey: 'workspace-running',
          homeIcon: 'mdi-view-column-outline',
          projectName: '别人的项目',
          privateUnreadTotal: 0,
          searchTitle: '搜索（⌘K）',
          menuOpen: false,
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
          homeActive: false,
          homeKey: 'workspace-running',
          homeIcon: 'mdi-view-column-outline',
          projectName: '选择项目',
          privateUnreadTotal: 0,
          searchTitle: '搜索（⌘K）',
          menuOpen: false,
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
          homeActive: false,
          homeKey: 'workspace-running',
          homeIcon: 'mdi-view-column-outline',
          projectName: '课程项目',
          privateUnreadTotal: 0,
          searchTitle: '搜索',
          menuOpen: false,
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
  {
    id: 'topic-rail-group-toggle',
    title: 'TopicRailGroupToggle',
    about: '一组被收起来的话题的组头：一个 chevron + 组名 + 条数。',
    file: 'src/components/topic-sidebar/TopicRailGroupToggle.vue',
    component: TopicRailGroupToggle,
    // 「其他话题」和「已归档」两处共用它——同一条侧栏里「一组被收起来的话题」
    // 只能有一种读法。
    needs: UI,
    states: [
      {
        name: '收着，里面还有新消息',
        note: '收起来时未读聚成一个点（不是数字）：别人话题里有几条与我无关，但那边有动静值得知道。',
        props: { label: '其他话题', count: 12, open: false, unread: true, unreadTitle: '其他话题里有新消息' },
        expect: '其他话题',
      },
      {
        name: '展开着',
        note: '展开着就没有那颗点：里面的事本来就在眼前。',
        props: { label: '其他话题', count: 12, open: true, unread: false, unreadTitle: '其他话题里有新消息' },
        expect: '12',
      },
      {
        name: '已归档那一组',
        note: '同一个组头，只多一个 archived 类（测试和样式凭它区分两个组头）。',
        props: {
          label: '已归档',
          count: 3,
          open: false,
          unread: false,
          unreadTitle: '归档话题里有新消息',
          archived: true,
        },
        expect: '已归档',
      },
    ],
  },
  {
    id: 'topic-rail-archived',
    title: 'TopicRailArchivedGroup',
    about: '列表最底下那组「已归档」：组头 + 收着的那批话题（新的在前）。',
    file: 'src/components/topic-sidebar/TopicRailArchivedGroup.vue',
    component: TopicRailArchivedGroup,
    // 收着的开关是这一件自己的状态，所以这里看到的是组头那一行；行本身（归档图标、
    // 种类词、行尾那颗「取消归档」）点开才有——那不在这份 props 能表达的范围内。
    needs: UI,
    states: [
      {
        name: '有一批归档话题',
        note: '没有归档话题时整件什么都不画（条件留在组件里，免得「最后一条也被取消归档」把开关复位）。',
        props: {
          rows: [RAIL_ROWS.archived],
          selectedTopicId: null,
          page: false,
          unread: true,
          unreadOf: () => 2,
        },
        expect: '已归档',
      },
      {
        name: '整页形态（手机）',
        note: '行更高，行尾那颗「取消归档」也撑到手指点得中。',
        props: {
          rows: [RAIL_ROWS.archived],
          selectedTopicId: RAIL_ROWS.archived.id,
          page: true,
          unread: false,
          unreadOf: () => 0,
        },
        expect: '已归档',
      },
    ],
  },
]
