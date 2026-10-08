/**
 * 公共站那几页在预览站里的条目：三个公共页共用的一圈外壳、首页、方案页，和首页里那段
 * 跟着滚动走的房间。
 *
 * 这一组的共同点是「不连后端也画得出来」：外壳的登录状态是从外面注入的
 * （`lib/publicVisitor.ts`），没人注入就按「没登录」画 —— 公共页本来就是给没登录的人
 * 看的，所以预览站里摆的正是访客看到的那一版（右上角「开始使用」）；首页里那段房间吃
 * 的是一句写好的脚本，摆出来的每一行都是产品里真在用的那些行。
 *
 * 首页和方案页没有参数：它们随滚动、语言和登录状态变，不随参数变，所以各摆它的第一屏。
 * 要看见别的状态，改的是环境（滚到哪一段、切哪种语言），不是参数 —— 那几件事在
 * `Landing.spec.ts` 里。
 *
 * 条目和别的分册没有两样（「这是什么 / 在哪儿 / 需要哪几样 / 看哪几格」，规矩见
 * `catalog.ts`），单放一份是因为 `catalog.ts` 已经顶到一千行的上限。这里的 `CatalogEntry`
 * 是 type-only 引用：`catalog.ts` 反过来要 `PUBLIC_ENTRIES` 这个值，运行时不构成循环。
 */
import type { CatalogEntry } from './catalog'

import Landing from '@/views/home/Landing.vue'
import LandingRoom from '@/views/home/LandingRoom.vue'
import LandingShell from '@/views/home/LandingShell.vue'
import Solutions from '@/views/home/Solutions.vue'

/** 这一组都走 router-link（导航、页脚的两条协议、页里那些「查看方案」），所以都要一条
 *  指得上的路由；外壳和方案页要 Vuetify 的画法（按钮、图标），要一套语言包。 */
const UI_T_R = ['vuetify', 'i18n', 'router'] as const

export const PUBLIC_ENTRIES: CatalogEntry[] = [
  {
    id: 'landing-shell',
    title: 'LandingShell',
    about:
      '三个公共页共用的一圈：顶栏（品牌、五个去处、切换语言、右上角那颗「开始使用」）、页脚，中间留一个口子给每一页自己的正文。',
    file: 'src/views/home/LandingShell.vue',
    component: LandingShell,
    needs: [...UI_T_R],
    states: [
      {
        name: '首页上的一圈',
        note: '在首页时导航停在「产品」上（这一页自己的章节），右上角那颗是访客看到的「开始使用」，点了去登录。',
        props: { page: 'home' },
        slot: '这里是首页的正文',
        expect: '这里是首页的正文',
        expectSelector: '.site-nav a[aria-current="page"]',
      },
      {
        name: '方案页上的一圈',
        note: '同一个外壳在方案页上：导航停在「方案」上，其余不变 —— 一个公共页换的只是 `page` 这一个词。',
        props: { page: 'solutions' },
        slot: '这里是方案页的正文',
        expect: '这里是方案页的正文',
        expectSelector: '.site-nav a[aria-current="page"]',
      },
      {
        name: '下载页上的一圈',
        note: '在下载页时「下载」那一项是当前项。窄屏上这五个去处收进右边那颗按钮里，宽的屏幕上它们就是一行链接。',
        props: { page: 'download' },
        slot: '这里是下载页的正文',
        expect: '这里是下载页的正文',
        expectSelector: '.site-nav a[aria-current="page"]',
      },
    ],
  },
  {
    id: 'landing',
    title: 'Landing',
    about:
      '公开首页：标语、那句自己会改词的定位（科研 / 课程 / 校企 …… 项目）、序、跟着滚动走的故事与房间、能用它做什么、资源与收尾。',
    file: 'src/views/home/Landing.vue',
    component: Landing,
    needs: [...UI_T_R],
    states: [
      {
        name: '整页',
        note: '访客打开首页看到的第一屏：标语、定位语、两颗去处；下面依次是序、故事、场景、资源。滚到哪一段、什么语言，都不是参数，所以这一格只看第一屏。',
        props: {},
        expect: '众智成事',
        expectSelector: 'h1.hero-title',
      },
    ],
  },
  {
    id: 'landing-room',
    title: 'LandingRoom',
    about:
      '公开首页里那个「房间」：和产品里一样的消息行，喂一句写好的脚本；`step` 是故事滚到第几段，每一段多出接下来发生的事。',
    file: 'src/views/home/LandingRoom.vue',
    component: LandingRoom,
    needs: ['vuetify', 'i18n'],
    states: [
      {
        name: '滚动之前：刚开始这一天的对话',
        note: '第 0 段只有此前的定标准与今天的开场：两天的分界、老师说的话、芝士的回执。',
        props: { step: 0 },
        expect: '评估标准就定这三条',
      },
      {
        name: '第 1 段：拆成三项',
        note: '拆完活，看板上多出三张任务卡（进行中），台词是「拆成三项同时推进」。',
        props: { step: 1 },
        expect: '可以。拆成三项同时推进',
        expectSelector: '.room-task',
      },
      {
        name: '第 2 段：原型交出来，右边开出预览',
        note: '原型那一版交出来之后，右边第一次开出预览面板 —— 那一行文件本身就是可点的交付物。',
        props: { step: 2 },
        expect: '预览 · 检索原型',
      },
      {
        name: '第 3 段：报告交上来，等人采纳',
        note: '试过原型，接下来交的是评估汇报：预览换成幻灯片，底下多一行「待李老师审阅」和退回 / 采纳两颗。',
        props: { step: 3 },
        expect: '预览 · 评估汇报',
      },
    ],
  },
  {
    id: 'solutions',
    title: 'Solutions',
    about:
      '方案页：写给把知是带进学校、企业和科研团队的那个人 —— 三种对象各一节（提供什么、怎么走），最后收在一封信上而不是注册上。',
    file: 'src/views/home/Solutions.vue',
    component: Solutions,
    needs: [...UI_T_R],
    states: [
      {
        name: '整页',
        note: '打开时停在「高校与机构」那一页签上：这一节说清给学院提供什么、学校的流程怎么走，右上角那颗是「预约交流」（写信到 ops@okcheese.com）。',
        props: {},
        expect: '事上磨练',
        expectSelector: '.solutions-tab[aria-selected="true"]',
      },
    ],
  },
]
