/**
 * 组件预览站里「侧栏那一组」的条目。
 *
 * 这几件是 TopicSidebar 拆出来、只管画的那几件：一行话题、顶上那组置顶入口、项目名
 * 那一行、一个组头、一组已归档、频道下面「展开其余」那一行，外加那颗未读角标。它们和别的条目没有两样，单独放一
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
import TopicRailLight from '@/components/topic-sidebar/TopicRailLight.vue'
import TopicRailMoreTasksRow from '@/components/topic-sidebar/TopicRailMoreTasksRow.vue'
import TopicRailPinnedRows from '@/components/topic-sidebar/TopicRailPinnedRows.vue'
import TopicRailRootRow from '@/components/topic-sidebar/TopicRailRootRow.vue'
import TopicRailRow from '@/components/topic-sidebar/TopicRailRow.vue'
import TopicRailTaskFold from '@/components/topic-sidebar/TopicRailTaskFold.vue'
import TopicRailTaskRow from '@/components/topic-sidebar/TopicRailTaskRow.vue'

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
    light: null,
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
    id: 'topic-rail-light',
    title: 'TopicRailLight',
    about:
      '行尾那盏灯：一个 8px 的点。红黄绿三种说这一行自己的状态，判据在 `lib/roomLight.ts`；蓝的说的是「这条归我」，由父级按归谁盖上（`useTopicRail.taskLight`），垫在那三种下面。这一件只管画。',
    file: 'src/components/topic-sidebar/TopicRailLight.vue',
    component: TopicRailLight,
    // `lightTitle` 要读词表（无障碍名字和 hover 那句话），所以得多装一个 i18n。
    needs: ['i18n'],
    states: [
      {
        name: '红：停下来报错，没人接手',
        note: '一轮停在报错上，五分钟没人接手。房间在等一位队友等太久了、交付被退回没人改，也是这一盏，悬停说的是哪一件。',
        props: { light: { kind: 'red', why: 'unfinished' } },
        expectSelector: '.row-light--red',
      },
      {
        name: '黄：有事等你',
        note: '有点名给你的验收卡、没答的决策请求，或者芝士停在一道只有你能回答的问题上。',
        props: { light: { kind: 'yellow' } },
        expectSelector: '.row-light--yellow',
      },
      {
        name: '绿：做完了，你还没看过',
        note: '点开看过就灭。',
        props: { light: { kind: 'green' } },
        expectSelector: '.row-light--green',
      },
      {
        name: '蓝：这条归我',
        note: '我负责的（以及没主的）任务行，在上面那几盏都没话说时才用它——红黄绿说的是这条活现在怎么了，蓝说的是这条归谁，谁轻谁重一眼看得出。别人的活不画蓝灯。它自己一个色相：红黄绿说的是状态，蓝说的是归谁。频道自身没有蓝灯；收起自己的任务时，频道行可汇总其蓝灯。',
        props: { light: { kind: 'blue' } },
        expectSelector: '.row-light--blue',
      },
    ],
  },
  {
    id: 'topic-rail-row',
    title: 'TopicRailRow',
    about: '频道行：名称前是任务和子话题的折叠箭头，行尾最多一盏状态灯，⋯ 独立占位；不显示头像和数字。',
    file: 'src/components/topic-sidebar/TopicRailRow.vue',
    component: TopicRailRow,
    // 一行不认识路由、也不取数：状态、折叠、未读全在 props 里（`row` 是
    // `lib/topicTree.ts` 的 `VisibleRow`，灯是 `lib/roomLight.ts` 算的）。
    needs: UI,
    states: [
      {
        name: '有队友在干活',
        note: '频道名保留完整可用空间；成员头像不在侧栏重复显示。',
        props: rowProps({ row: RAIL_ROWS.working }),
        expect: '写第 4 章的教案',
      },
      {
        name: '有事等你（黄灯）',
        note: '行尾一盏黄灯：有点名给你的验收卡、没答的决策请求，或者芝士停在一道只有你能回答的问题上。未读的 @ 不点这盏灯；未读通过名称加粗提示，不显示数字。',
        props: rowProps({ row: RAIL_ROWS.awaits, light: { kind: 'yellow' } }),
        expect: '决定这学期用哪本教材',
      },
      {
        name: '做完了、你还没看过（绿灯）',
        note: '这里的活都交付完了，你还没点开看过。点开就灭。',
        props: rowProps({ row: RAIL_ROWS.working, light: { kind: 'green' } }),
        expectSelector: '.row-light--green',
      },
      {
        name: '停下来报错、没人接手（红灯）',
        note: '一轮停在报错上，五分钟没人接手。这一盏压过黄和绿——同时成立时只画它。',
        props: rowProps({ row: RAIL_ROWS.working, light: { kind: 'red', why: 'unfinished' } }),
        expectSelector: '.row-light--red',
      },
      {
        name: '一位成员卡住了',
        note: '原来成员头像上的卡住提示并入行尾唯一的灯，现有卡住判据不变。',
        props: rowProps({ row: RAIL_ROWS.stalled, stalled: true, marks: RAIL_MARKS.working }),
        expect: '把成绩单导出成 CSV',
      },
      {
        name: '收起来的父话题',
        note: '折叠开关保持中性，收起来的后代状态与频道自身状态在行尾合为一盏灯；隐藏未读仍让名称加粗。',
        props: rowProps({ row: RAIL_ROWS.collapsed, toggleTitle: '展开：里面有待处理的事项' }),
        expect: '期末复习',
      },
      {
        name: '子话题',
        note: '缩进一级，左边保留竖向引导线；未读通过名称加粗提示。',
        props: rowProps({ row: RAIL_ROWS.sub }),
        expect: '第 3 题：为什么天空是蓝的',
      },
      {
        name: '整页形态（手机）',
        note: '任务箭头和 ⋯ 使用独立触控区域，⋯ 常驻；长按仍可打开同一组操作。',
        props: rowProps({
          row: RAIL_ROWS.working,
          page: true,
          selected: true,
          taskCount: 5,
          tasksCollapsed: true,
          taskLight: { kind: 'yellow' },
        }),
        expect: '写第 4 章的教案',
      },
      {
        name: '挂着任务：名称前的折叠箭头',
        note: '任务箭头在名称前；⋯ 在行尾独立占位，出现时不移动名称或遮挡状态灯。',
        props: rowProps({ row: RAIL_ROWS.working, menuOpen: true, taskCount: 5 }),
        expect: '写第 4 章的教案',
      },
      {
        name: '任务块收起：状态合成一盏灯',
        note: '不显示任务数量，收起来的任务状态与频道状态合成一盏灯，红黄绿优先于蓝。',
        props: rowProps({
          row: RAIL_ROWS.awaits,
          taskCount: 5,
          tasksCollapsed: true,
          taskLight: { kind: 'yellow' },
        }),
        expectSelector: '.row-light--yellow',
      },
      {
        name: '同层：子频道和任务都能折叠',
        note: '两个独立箭头共用固定宽度的前置区域，名称与同层其他频道对齐。',
        props: rowProps({ row: { ...RAIL_ROWS.working, hasChildren: true, collapsed: false }, taskCount: 5 }),
        expectSelector: '.subtree-toggle',
      },
      {
        name: '同层：只有子频道',
        note: '没有任务箭头时，前置区域宽度仍保持不变。',
        props: rowProps({ row: { ...RAIL_ROWS.working, hasChildren: true, collapsed: false } }),
        expectSelector: '.subtree-toggle',
      },
      {
        name: '同层：只有任务',
        note: '任务箭头在名称前，与只有子频道或没有子项的频道对齐。',
        props: rowProps({ row: RAIL_ROWS.working, taskCount: 5 }),
        expectSelector: '.task-fold__btn',
      },
      {
        name: '私密频道有任务',
        note: '任务箭头不替代锁；私密频道的标记与提示仍然可见。',
        props: rowProps({
          row: { ...RAIL_ROWS.working, topic: { ...RAIL_ROWS.working.topic, members_only: true } },
          taskCount: 1,
        }),
        expectSelector: '.mdi-lock-outline',
      },
      {
        name: '手机：两个独立折叠入口',
        note: '子频道和任务各有独立的触屏落点，按钮之间不互相覆盖。',
        props: rowProps({
          row: { ...RAIL_ROWS.working, hasChildren: true, collapsed: false },
          page: true,
          selected: true,
          taskCount: 5,
          tasksCollapsed: true,
          taskLight: { kind: 'yellow' },
        }),
        expectSelector: '.subtree-toggle',
      },
    ],
  },
  {
    id: 'topic-rail-pinned',
    title: 'TopicRailPinnedRows',
    about: '项目名下面那一行：总览和资料库，和频道行同一种行。别的页都进项目名菜单，这里不长。',
    file: 'src/components/topic-sidebar/TopicRailPinnedRows.vue',
    component: TopicRailPinnedRows,
    // 只读词表和 props：哪几页露出来了、当前在哪一页，都是父级算好递进来的。
    needs: ['vuetify', 'i18n'],
    states: [
      {
        name: '站在总览上',
        note: '选中态和频道行同一套。',
        props: {
          pages: RAIL_PAGES,
          routeName: 'workspace-overview',
          terms: RAIL_TERMS,
          page: false,
        },
        expect: '总览',
      },
      {
        name: '站在资料库上',
        note: '同一个组件，换一个当前页。',
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
        note: '整页形态里这几页在点项目名弹出的面板里，这里不画。',
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
        note: '选中态和未读名称加粗与普通频道行一致，不显示未读数字。',
        props: {
          rootTopic: RAIL_ROOT_TOPIC,
          selectedTopicId: 't-root',
          page: false,
          unreadOf: (id: string) => (id === 't-root' ? 3 : 0),
        },
        expect: '综合',
      },
      {
        name: '做完了、你还没看过',
        note: '行尾那盏灯和频道行、任务行是同一件（`TopicRailLight`），算它的判据也同一份。',
        props: {
          rootTopic: RAIL_ROOT_TOPIC,
          selectedTopicId: null,
          page: false,
          unreadOf: () => 0,
          lightOf: () => ({ kind: 'green' }),
        },
        expectSelector: '.row-light--green',
      },
      {
        name: '综合只汇总收起的任务',
        note: '频道自身没有状态时，收起的任务仍通过唯一状态灯提示。',
        props: {
          rootTopic: RAIL_ROOT_TOPIC,
          selectedTopicId: null,
          page: false,
          unreadOf: () => 0,
          lightOf: () => null,
          taskCount: 1,
          tasksCollapsed: true,
          taskLight: { kind: 'blue' },
        },
        expectSelector: '.row-light--blue',
      },
    ],
  },
  {
    id: 'topic-rail-header',
    title: 'TopicRailHeader',
    about: '项目名那一行：点名字弹出项目菜单（资料库之外的几页：总览、项目文档、设置），右边是搜索。',
    file: 'src/components/topic-sidebar/TopicRailHeader.vue',
    component: TopicRailHeader,
    // 名字连着 ⌄ 是一个按钮，点下去是菜单。
    // 整页形态下这一行填进顶栏（Teleport），另外两个形态里它就在原地。
    needs: ['vuetify', 'i18n', 'router'],
    states: [
      {
        name: '所有者',
        note: '项目名上面一行是它所属的团队，点了去团队的项目列表。菜单里是总览、项目文档、资料库之外的几页、项目设置和「转让项目」；成员上挂着私聊未读。',
        props: {
          page: false,
          column: false,
          projectName: '课程项目',
          team: { label: '知是', handle: 'zhishi' },
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
          team: null,
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
          team: null,
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
          team: null,
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
  {
    id: 'topic-rail-task-row',
    title: 'TopicRailTaskRow',
    about: '频道下面的任务行，与展开其余任务的文字左缘和竖向引导线对齐。',
    file: 'src/components/topic-sidebar/TopicRailTaskRow.vue',
    component: TopicRailTaskRow,
    needs: UI,
    args: {
      task: { id: 'rail-task-demo', room_id: 'room-1', title: '频道行任务', title_source: 'human', status: 'open' },
      selected: false,
    },
    states: [
      {
        name: '频道下面的任务',
        note: '与同频道展开入口保持相同的文字缩进。',
        props: { depth: 0 },
        expect: '频道行任务',
      },
      {
        name: '子频道下面的任务',
        note: '跟随子频道层级缩进，文字与展开入口对齐。',
        props: { depth: 1 },
        expect: '频道行任务',
      },
    ],
  },
  {
    id: 'topic-rail-more-tasks-row',
    title: 'TopicRailMoreTasksRow',
    about:
      '频道下面任务那几行的最后一行：侧栏先只挂和我有关的几条，这一行就地展开这个频道其余进行中的任务，再点一下收回。它不换页。',
    file: 'src/components/topic-sidebar/TopicRailMoreTasksRow.vue',
    component: TopicRailMoreTasksRow,
    // 那个箭头是 v-icon，所以要 Vuetify；字由调用方递进来，不读词表。
    needs: UI,
    args: { channelId: 'room-1' },
    states: [
      {
        name: '收着',
        note: '说清还有几个没挂出来；箭头朝下。',
        props: { label: '展开其余 7 个', expanded: false },
        expect: '展开其余 7 个',
      },
      {
        name: '展开了',
        note: '其余的已经接在上面，这一行改成收起；箭头朝上。',
        props: { label: '收起', expanded: true },
        expect: '收起',
      },
      {
        name: '挂在子频道下面',
        note: '竖线和文字左缘跟着频道在树里的层级往里缩。',
        props: { label: '展开其余 2 个', expanded: false, depth: 1 },
        expect: '展开其余 2 个',
      },
    ],
  },
  {
    id: 'topic-rail-task-fold',
    title: 'TopicRailTaskFold',
    about: '频道名前的任务折叠箭头。只切换任务块展开状态，收起后的信号由频道行统一合并为一盏灯，不再显示数量。',
    file: 'src/components/topic-sidebar/TopicRailTaskFold.vue',
    component: TopicRailTaskFold,
    // 箭头来自 Vuetify，展开和收起提示读取词表。
    needs: ['vuetify', 'i18n'],
    states: [
      {
        name: '开着：任务就挂在下面',
        note: '只有一个朝下的箭头；开关独立于频道名称，操作不会切换频道。',
        props: { collapsed: false },
        expectSelector: '.task-fold__btn',
      },
      {
        name: '收起来了',
        note: '箭头朝右，保留再次展开的入口；状态灯放在频道行尾。',
        props: { collapsed: true },
        expectSelector: '.task-fold__btn',
      },
      {
        name: '触屏',
        note: '触屏入口使用独立的 44px 命中区域，不覆盖名称。',
        props: { collapsed: true, page: true },
        expectSelector: '.task-fold__btn',
      },
    ],
  },
]
