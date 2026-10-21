/**
 * 命令面板拆出来的四件在预览站里的条目：顶上那一栏、中间那一列结果、桌面上那份
 * 「还能做什么」的清单、底栏。
 *
 * 单独一份的理由和 `catalogLibrary.ts` 那几份一样：这四件是同一个浮层的四块
 * （合起来才是 `/` 唤出来的那个面板），搁在一块儿才看得清哪块管什么。
 *
 * 四件都只吃 props：搜什么、选中第几条、清单开没开、键怎么按，全在
 * `CommandPalette.vue` 和 `src/commands/palette/` 那两个 composable 里，所以这里
 * 不用装假后端 —— 样例数据按 props 就近造在下面（`catalogFixtures.ts` 已经贴着仓库
 * 那一千行的上限）。同一份形状在面板里由 `src/commands/palette/results.ts` 算出来。
 */
import type { MenuCommand } from '@/commands'
import type { ResultGroup } from '@/commands/palette/results'
import type { PaletteAsk } from '@/commands/palette/state'
import type { CatalogEntry } from './catalog'

import PaletteActions from '@/commands/palette/PaletteActions.vue'
import PaletteFoot from '@/commands/palette/PaletteFoot.vue'
import PaletteInput from '@/commands/palette/PaletteInput.vue'
import PaletteResults from '@/commands/palette/PaletteResults.vue'

/** 面板里搜「登录」时那一屏的形状：上了范围的一组，加一组此刻能做的操作。 */
const GROUPS: ResultGroup[] = [
  {
    key: 'recent',
    label: 'navigation.palette.recent',
    rows: [
      {
        id: 'topic:t-login',
        title: '登录页改成深色',
        subtitle: '芝士平台',
        icon: 'mdi-pound',
        // 名字里对上输入的那一段（「登录」），列表按它画高亮。
        range: [0, 2],
        badge: { text: '待审阅', tone: 'warn' },
      },
      { id: 'member:m-wang', title: '王长鑫', subtitle: '@wangchangxin', icon: 'mdi-account-outline', range: [0, 1] },
    ],
  },
  {
    key: 'commands',
    label: 'navigation.palette.commands',
    rows: [{ id: 'command:dark', title: '切换到深色外观', icon: 'mdi-theme-light-dark', range: null, verb: '执行' }],
  },
]

/** 搜「哈希」时「题目」那一组：我加入的空间里的题目，下面一行是空间名。 */
const TASK_GROUPS: ResultGroup[] = [
  {
    key: 'space-tasks',
    label: 'navigation.palette.spaceTasks',
    rows: [
      {
        id: 'space-task:41',
        title: '实现一个开放寻址哈希表',
        subtitle: '数据结构（2026 秋）',
        icon: 'mdi-trophy-outline',
        range: [8, 10],
      },
      {
        id: 'space-task:42',
        title: '一致性哈希的负载实验',
        subtitle: '分布式系统',
        icon: 'mdi-trophy-outline',
        range: [3, 5],
      },
    ],
  },
]

/** 一条结果上按右键（手机上是长按）能做的事：回车那一件在最前，会坏事的那一件带 danger。 */
const ACTIONS: MenuCommand[] = [
  { id: 'topic:open', title: '打开频道', icon: 'mdi-pound' },
  { id: 'topic:mute', title: '静音 1 天', icon: 'mdi-bell-off-outline' },
  { id: 'topic:archive', title: '归档', icon: 'mdi-archive-outline' },
  { id: 'topic:delete', title: '删除频道', icon: 'mdi-trash-can-outline', danger: true },
]

/** 面板替人问的一句话：重命名一个频道时要的新名字。预览站里没人接这一句，所以交出去是空动作。 */
const ASK: PaletteAsk = {
  title: '重命名频道',
  placeholder: '新名字',
  value: '芝士平台',
  submit: () => undefined,
}

export const PALETTE_ENTRIES: CatalogEntry[] = [
  {
    id: 'palette-input',
    title: 'PaletteInput',
    about: '命令面板顶上那一栏：放大镜（手机上换成返回）、范围 chip、输入框、搜索进度条。',
    file: 'src/commands/palette/PaletteInput.vue',
    component: PaletteInput,
    needs: ['vuetify', 'i18n'],
    states: [
      {
        name: '一进来，还没打字',
        note: '只有放大镜和一个空输入框；下面有什么由父级决定，这一栏不管。',
        props: {
          mdAndUp: true,
          ask: null,
          scopeName: undefined,
          searching: false,
          expanded: false,
          activeDescendant: undefined,
          input: '',
          askText: '',
        },
        expectSelector: 'input[role="combobox"]',
      },
      {
        name: '在一个项目里搜',
        note: '范围 chip 上是项目名，点它去掉范围、跨项目只找名字；远程还没回来时进度条亮着。',
        props: {
          mdAndUp: true,
          ask: null,
          scopeName: '芝士平台',
          searching: true,
          expanded: true,
          activeDescendant: 'palette-option-0',
          input: '登录',
          askText: '',
        },
        expect: '芝士平台',
      },
      {
        name: '面板在问一句话',
        note: '要问的时候这一栏换成那一句的答案：同一时刻只画得出来一个输入框，这一格画的是另一个。',
        props: {
          mdAndUp: true,
          ask: ASK,
          scopeName: undefined,
          searching: false,
          expanded: false,
          activeDescendant: undefined,
          input: '',
          askText: '登录入口',
        },
        expect: '重命名频道',
      },
      {
        name: '手机上一整页',
        note: '手机上面板铺满整页，放大镜的位置换成返回。',
        props: {
          mdAndUp: false,
          ask: null,
          scopeName: undefined,
          searching: false,
          expanded: false,
          activeDescendant: undefined,
          input: '',
          askText: '',
        },
        expectSelector: '[aria-label="关闭"]',
      },
    ],
  },
  {
    id: 'palette-results',
    title: 'PaletteResults',
    about: '面板中间那一列结果：分组、每行的图标和名字（对上的几个字高亮）、右侧的角标。',
    file: 'src/commands/palette/PaletteResults.vue',
    component: PaletteResults,
    needs: ['vuetify', 'i18n'],
    states: [
      {
        name: '搜到的结果，分两组',
        note: '高亮的那一行是此刻回车会去的那一条；名字里对上输入的两个字带 <mark>。',
        props: { groups: GROUPS, selected: 0, input: '登录', searching: false, mdAndUp: true },
        expect: '登录页改成深色',
      },
      {
        name: '搜到空间里的题目',
        note: '「题目」一组只列我加入的空间里的题目；下面一行写着在哪个空间，点开进题目页。',
        props: { groups: TASK_GROUPS, selected: 0, input: '哈希', searching: false, mdAndUp: true },
        expect: '实现一个开放寻址哈希表',
      },
      {
        name: '什么都没搜到',
        note: '真打了字、又一条都没对上才说这一句；还在等远程结果时不说 —— 那是有没有都还不知道。',
        props: { groups: [], selected: 0, input: '登录页改成黑', searching: false, mdAndUp: true },
        expect: '暂无结果',
      },
      {
        name: '手机上一整页',
        note: '手机上面板是一整页，行更高，列表也给底部安全区让位。',
        props: { groups: GROUPS, selected: 0, input: '', searching: false, mdAndUp: false },
        expectSelector: '.palette__list--page',
      },
    ],
  },
  {
    id: 'palette-actions',
    title: 'PaletteActions',
    about: '一条结果「还能做什么」在桌面上的那份清单：贴在面板右下角，头上写着对的是哪一条。',
    file: 'src/commands/palette/PaletteActions.vue',
    component: PaletteActions,
    needs: ['vuetify'],
    states: [
      {
        name: '一条频道能做的事',
        note: '回车那一件排在最前；会坏事的「删除频道」用告警色。手机上同一份清单换成从底部升起的动作条。',
        props: { title: '登录页改成深色', actions: ACTIONS, selected: 0 },
        expect: '静音 1 天',
      },
      {
        name: '停在「删除频道」上',
        note: '停在哪一件由父级拿着（键盘也是它管的），这里只把停在的那一件画重。',
        props: { title: '登录页改成深色', actions: ACTIONS, selected: 3 },
        expectSelector: '.palette__action--selected.palette__action--danger',
      },
    ],
  },
  {
    id: 'palette-foot',
    title: 'PaletteFoot',
    about: '面板底栏：左边写此刻回车会做什么，右边写还有哪几个键能用。手机上这一栏不存在。',
    file: 'src/commands/palette/PaletteFoot.vue',
    component: PaletteFoot,
    needs: ['i18n'],
    states: [
      {
        name: '在问一句话',
        note: '面板在等人答话：回车是「保存」，Esc 是不问了。',
        props: {
          asking: true,
          actionTitle: undefined,
          verb: undefined,
          canSearchIn: false,
          hasMore: false,
          hasTarget: false,
        },
        expect: '保存',
      },
      {
        name: '选中的是一条命令',
        note: '左半边是这一条回车做的那件事；它还有别的事可做，所以 Tab 写「更多操作」。',
        props: {
          asking: false,
          actionTitle: undefined,
          verb: '执行',
          canSearchIn: false,
          hasMore: true,
          hasTarget: false,
        },
        expect: '更多操作',
      },
      {
        name: '停在清单里的某一件上',
        note: '清单开着时左半边换成那一件的名字，右边的键只剩返回。',
        props: {
          asking: false,
          actionTitle: '归档',
          verb: undefined,
          canSearchIn: false,
          hasMore: false,
          hasTarget: false,
        },
        expect: '归档',
      },
      {
        name: '进得了项目，也去得了别处',
        note: '这一条还能进它所在的项目里搜（Tab 写「项目内搜索」）；有地方可去时给按住 ⌘/Ctrl 回车的提示。',
        props: {
          asking: false,
          actionTitle: undefined,
          verb: '打开',
          canSearchIn: true,
          hasMore: true,
          hasTarget: true,
        },
        expect: '项目内搜索',
      },
    ],
  },
]
