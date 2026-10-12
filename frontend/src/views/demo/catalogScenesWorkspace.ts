/**
 * 预览站里「容器页面拆出来的那半张画面」这一组：几个路由页各拆成一个页面（`XView.vue`
 * —— 读地址、取数、跳转）加一个画面（`XViewView.vue` —— 只收 props、只发事件）。这里
 * 摆的是画面那一半，所以每一件都不带路由、不带接口就能单独渲染。
 *
 * 这一组有六件：设备审批（命令行把机器交给你批准）、已归档的项目（只有所有者看得到）、
 * 打开预览、打开网站（两件同形的落地页），以及项目里的例行事务和内容搜索两个整页。
 *
 * 条目和别的分册没有两样（「这是什么 / 在哪儿 / 需要哪几样 / 看哪几格」，规矩见
 * `catalog.ts`），单放一份是因为 `catalog.ts` 已经顶到一千行的上限。这里的 `CatalogEntry`
 * 是 type-only 引用，`catalog.ts` 反过来要 `SCENES_WORKSPACE_ENTRIES` 这个值，运行时不
 * 构成循环。数据见 `catalogScenesWorkspaceFixtures.ts`。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import {
  allSections,
  archivedProject,
  CONTENT_KINDS,
  deviceApproval,
  routine,
  ROUTINE_RUNS,
  ROUTINES,
  SEARCH_COUNTS,
  SEARCH_HITS,
  SEARCH_HITS_LIBRARY_ONLY,
  searchSection,
  topic,
  USER_NAMES,
} from './catalogScenesWorkspaceFixtures'

import ConnectViewView from '@/views/ConnectViewView.vue'
import MyArchivedProjectsViewView from '@/views/MyArchivedProjectsViewView.vue'
import PreviewOpenViewView from '@/views/PreviewOpenViewView.vue'
import ProjectRoutinesViewView from '@/views/ProjectRoutinesViewView.vue'
import ProjectSearchViewView from '@/views/ProjectSearchViewView.vue'
import SiteOpenViewView from '@/views/SiteOpenViewView.vue'
import { library } from '@/views/workspace/search/library.palette'
import { messages } from '@/views/workspace/search/messages.palette'

/** 六件都要 Vuetify 的画法和一套语言包。去处（去登录、进某个房间）由容器给，画面
 *  自己没有路由就退化成点不动的字，照旧画得出来。 */
const UI_T: CatalogNeed[] = ['vuetify', 'i18n']

export const SCENES_WORKSPACE_ENTRIES: CatalogEntry[] = [
  {
    id: 'connect-view',
    title: 'ConnectViewView',
    about:
      '设备审批页的画面：命令行把 `<origin>/connect?code=…` 交给已登录的人，他在这里给这台机器起个名、点一下，机器就归他所有、此后可以接活。这一半只收 props、只发 approve —— 读地址、预填命令行提上来的名字、调后端在容器 `ConnectView.vue` 里。',
    file: 'src/views/ConnectViewView.vue',
    component: ConnectViewView,
    needs: UI_T,
    args: { loading: false, error: null, approved: null, proposedName: '' },
    states: [
      {
        name: '链接里没有设备码',
        note: '命令行给的地址缺了 code：先说清怎么进来（跑哪条命令），不给批准按钮。',
        props: { code: '', loggedIn: true },
        expect: '链接里没有设备码',
      },
      {
        name: '还没登录',
        note: '批准会把机器绑到一个真实账号上，所以没登录时先请他去登录；登录完路由把他带回这一页。',
        props: { code: 'ABCD-1234', loggedIn: false },
        expect: '请先登录，再批准这台设备归你所有',
      },
      {
        name: '等批准',
        note: '地址里有码、人也登录了：摆出设备码和名字那一栏，名字已经填上命令行提上来的机器名，改一改就能批。',
        props: { code: 'ABCD-1234', loggedIn: true, proposedName: 'andy-macbook' },
        expect: 'ABCD-1234',
      },
      {
        name: '审批失败',
        note: '点下去后端拒了：把那句话摆出来，码和名字都还在，可以再点一次。',
        props: { code: 'ABCD-1234', loggedIn: true, error: '审批失败', proposedName: 'andy-macbook' },
        expect: '审批失败',
      },
      {
        name: '已批准',
        note: '批完了：一颗勾，说清设备「叫什么」已绑到你，剩下的交给命令行的 `link connect` 去完成。',
        props: { code: 'ABCD-1234', loggedIn: true, approved: deviceApproval() },
        expect: '设备已连接',
      },
    ],
  },
  {
    id: 'archived-projects-view',
    title: 'MyArchivedProjectsViewView',
    about:
      '「已归档的项目」那一页的画面：归档了的项目不在任何列表里，只在它的所有者这里列成一列，每行写着什么时候归档的，和一颗「取消归档」。取列表、调后端、刷新项目清单在容器 `MyArchivedProjectsView.vue` 里。',
    file: 'src/views/MyArchivedProjectsViewView.vue',
    component: MyArchivedProjectsViewView,
    needs: UI_T,
    args: { loading: false, error: '', restoring: '' },
    states: [
      {
        name: '一个都没有',
        note: '没归档过任何项目：就地说一句「暂无已归档的项目」，不是一块空白。',
        props: { projects: [] },
        expect: '暂无已归档的项目',
      },
      {
        name: '归档过的项目',
        note: '两行：项目名、归档于哪一天，右边一颗「取消归档」把它送回项目清单。',
        props: {
          projects: [
            archivedProject(),
            archivedProject({ id: 'p-weekly', name: '周报', archived_at: '2026-09-30T00:00:00Z' }),
          ],
        },
        expect: '毕业设计',
      },
      {
        name: '正在取消归档',
        note: '这一行的那颗按钮转起来了：这一行在忙，别的行照旧能点。',
        props: { projects: [archivedProject()], restoring: 'p-grad' },
        expectSelector: '.v-btn--loading',
      },
      {
        name: '读列表失败',
        note: '列表没读出来：一行错误留在原地，不是「暂无」—— 那会让失败看着像本来就没有。',
        props: { projects: [], error: '加载失败' },
        expect: '加载失败',
      },
    ],
  },
  {
    id: 'preview-open-view',
    title: 'PreviewOpenViewView',
    about:
      '预览落地页的画面：地址里带着一个房间和一份预览路径，人打开时先看到这一屏 —— 还没登录、打开失败、或者正在打开。取预览会话、把它交给内容站在容器 `PreviewOpenView.vue` 里。',
    file: 'src/views/PreviewOpenViewView.vue',
    component: PreviewOpenViewView,
    needs: UI_T,
    states: [
      {
        name: '要登录',
        note: '预览只给项目成员看：没登录时先说这一句，再给一颗去登录的按钮。',
        props: { loading: false, error: '', needsLogin: true },
        expect: '这个预览仅项目成员可访问，请先登录',
      },
      {
        name: '打开失败',
        note: '取会话那一步失败了：把服务端那句话摆出来，给一颗重试。',
        props: { loading: false, error: '接口返回 502', needsLogin: false },
        expect: '接口返回 502',
      },
      {
        name: '正在打开',
        note: '会话在路上的时候只有一颗转圈，读屏念的是「正在打开预览」。',
        props: { loading: true, error: '', needsLogin: false },
        expectSelector: '[aria-label="正在打开预览"]',
      },
    ],
  },
  {
    id: 'site-open-view',
    title: 'SiteOpenViewView',
    about:
      '网站落地页的画面：和打开预览同形，只是去的是项目的对外网站。地址里带着项目，人在这里看到「要登录 / 打开失败 / 正在打开」三种样子之一；取会话、把只读授权 POST 给内容站在容器 `SiteOpenView.vue` 里。',
    file: 'src/views/SiteOpenViewView.vue',
    component: SiteOpenViewView,
    needs: UI_T,
    states: [
      {
        name: '要登录',
        note: '网站只给项目成员看：没登录时先说这一句，再给一颗去登录的按钮。',
        props: { loading: false, error: '', needsLogin: true },
        expect: '这个网站仅项目成员可访问，请先登录',
      },
      {
        name: '打开失败',
        note: '取会话那一步失败了：把服务端那句话摆出来，给一颗重试。',
        props: { loading: false, error: '接口返回 502', needsLogin: false },
        expect: '接口返回 502',
      },
      {
        name: '正在打开',
        note: '会话在路上的时候只有一颗转圈，读屏念的是「正在打开网站」。',
        props: { loading: true, error: '', needsLogin: false },
        expectSelector: '[aria-label="正在打开网站"]',
      },
    ],
  },
  {
    id: 'project-routines-view',
    title: 'ProjectRoutinesViewView',
    about:
      '项目里「例行事务」那一整页的画面：这个项目所有房间的规则，一条一行；等确认的草稿摊开给人读，已经在跑的写清下次什么时候跑，页头两颗命令由页壳画。取数、读地址里的 `?routine=`、权限都在容器 `ProjectRoutinesView.vue` 里。',
    file: 'src/views/ProjectRoutinesViewView.vue',
    component: ProjectRoutinesViewView,
    needs: UI_T,
    args: {
      agentName: '芝士',
      runs: ROUTINE_RUNS,
      openId: null,
      busy: '',
      loading: false,
      error: '',
      roomNames: { 'topic-weekly': '周会' },
      userNames: USER_NAMES,
      rooms: [topic()],
      defaultRoom: 'topic-weekly',
      formOpen: false,
      editing: null,
      saving: false,
      formError: '',
    },
    states: [
      {
        name: '还没有规则',
        note: '一条规则都没有：就地说一句「还没有例行事务」，并告诉他怎么来一条。',
        props: { routines: [] },
        expect: '还没有例行事务',
      },
      {
        name: '一条等确认、一条在跑',
        note: '草稿摆在前面的「等你确认」那一栏里，摊开写着它要做什么、用哪些资料、结果放哪；已经在跑的那条写在下面，带它的状态和下次时间。',
        props: { routines: ROUTINES },
        expect: '等你确认',
      },
      {
        name: '正在读规则',
        note: '规则还在路上的时候只有一颗转圈，读屏念的是「读取例行事务」。',
        props: { routines: [], loading: true },
        expectSelector: '[aria-label="读取例行事务"]',
      },
      {
        name: '读规则失败',
        note: '列表没读出来：一句话留在最上面（role=alert），不是「还没有规则」—— 那会让失败看着像本来就没有。',
        props: { routines: [], error: '未能读取例行事务' },
        expect: '未能读取例行事务',
      },
      {
        name: '展开执行记录',
        note: '点开某一条的执行记录：它下面接着一串，每次执行的成败、用了多久、结果在哪都在那儿。',
        props: { routines: [routine()], openId: 'routine-weekly' },
        expect: '整理好了 12 条讨论',
      },
    ],
  },
  {
    id: 'project-search-view',
    title: 'ProjectSearchViewView',
    about:
      '内容搜索结果页的画面：命令面板里每类只列前几条，这一页看全 —— 搜索框、栏目页签、每一段结果，和底部的加载 / 空 / 出错三种状态。取数、地址（`?q=…&kind=…`）和翻页在容器 `ProjectSearchView.vue` 里，这一半只收 props、只把「输入停了」「点了某一栏」「滚到底」抛上去。',
    file: 'src/views/ProjectSearchViewView.vue',
    component: ProjectSearchViewView,
    needs: UI_T,
    args: { kinds: CONTENT_KINDS, loading: false, failed: false, moreFailed: false, errorReason: '', exhausted: true },
    states: [
      {
        name: '还没输词',
        note: '刚进来、还没打字：只有一个搜索框，没有页签也没有结果。',
        props: { query: '', kindId: null, counts: null, sections: [] },
        expectSelector: 'input[type="search"]',
      },
      {
        name: '「全部」那一栏',
        note: '每类只列前几条、各自一小段，段头上写着这一类一共有多少条，「查看全部」进这一类看全 —— 命中的字在标题里标出来。',
        props: { query: '进展', kindId: null, counts: SEARCH_COUNTS, sections: allSections() },
        expect: '查看全部 12 条',
      },
      {
        name: '只看「资料库」一栏',
        note: '点进某一栏就只有这一类，段头不画（页签已经说明看的是哪一栏）：这里按文件名搜到一份资料。',
        props: {
          query: 'reports',
          kindId: 'library',
          counts: SEARCH_COUNTS,
          sections: [searchSection(library, SEARCH_HITS_LIBRARY_ONLY)],
        },
        expect: '2026-09-29.md',
      },
      {
        name: '没有结果',
        note: '搜了但没有一条对上：就地一句「暂无结果」，并给一个「清空」把词清掉、回到还没输的状态。',
        props: { query: 'zzz', kindId: null, counts: null, sections: [] },
        expect: '暂无结果',
      },
      {
        name: '搜索失败',
        note: '第一次搜就失败了：结果那一块换成错误和一颗重试，搜索框和页签留在原地。',
        props: { query: '进展', kindId: null, counts: null, sections: [], failed: true, errorReason: '接口返回 500' },
        expect: '搜索失败，稍后重试',
      },
      {
        name: '翻页失败',
        note: '滚到底加载下一页失败了：已经到手的那一段不扔，只在它下面补上错误和一颗重试。',
        props: {
          query: '进展',
          kindId: 'messages',
          counts: SEARCH_COUNTS,
          sections: [searchSection(messages, SEARCH_HITS)],
          exhausted: false,
          moreFailed: true,
          errorReason: '接口返回 504',
        },
        expect: '下周三的课改到线上，有课件的提前发我',
      },
      {
        name: '正在搜索',
        note: '结果在路上的时候只有一颗转圈，读屏念的是「正在搜索」。',
        props: { query: '进展', kindId: null, counts: null, sections: [], loading: true },
        expectSelector: '[aria-label="正在搜索"]',
      },
    ],
  },
]
