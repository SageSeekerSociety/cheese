/**
 * 预览站里「整页」那一组：一项产物交付过的每一版，和小队的对外一面。
 *
 * 这两件以前要一整套东西才看得出来（一条真产物、一份真小队、路由、外壳），现在各自
 * 只吃 props：版本表收一个 `versions` 数组和它所在的 `projectId`，「加入」的函数由调用
 * 方给；小队简介收一份 `Team`。所以它们能单独摆进来 —— 这里展示的正是产品里真会出现的
 * 那几种状态（刚交付/file/link/merge、没交付过、版本多到要收起来；没加入要审批、不用
 * 审批、已经是成员、申请交过了）。
 *
 * 条目和别的分册没有两样（「这是什么 / 在哪儿 / 需要哪几样 / 看哪几格」，规矩见
 * `catalog.ts`），单放一份是因为 `catalog.ts` 已经顶到一千行的上限。这里的 `CatalogEntry`
 * 是 type-only 引用，`catalog.ts` 反过来要 `VIEW_ENTRIES` 这个值，运行时不构成循环。
 * 数据见 `catalogSharedFixtures.ts`。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import {
  ARTIFACT_VERSIONS,
  ARTIFACT_VERSIONS_MANY,
  ARTIFACT_VERSIONS_WITHOUT_FILE,
  teamProfile,
} from './catalogSharedFixtures'

import ArtifactVersionList from '@/views/artifact/ArtifactVersionList.vue'
import SpaceReviewNoticeView from '@/views/spaces/detail/SpaceReviewNoticeView.vue'
import TeamProfile from '@/views/teams/TeamProfile.vue'

/** 两件都要 Vuetify 的画法、一套语言包，卡片按钮还要一条 name 指得上的路由。 */
const UI_T_R: CatalogNeed[] = ['vuetify', 'i18n', 'router']

/** 「加入」这一趟接口由调用方给，预览站上走一遍就完：只看画出来的样子。 */
const join = async () => {}

export const VIEW_ENTRIES: CatalogEntry[] = [
  {
    id: 'artifact-version-list',
    title: 'ArtifactVersionList',
    about:
      '一项产物交付过的每一版，最新的在最上面：每一行说清这次改了什么、谁什么时候认的、在哪次对话里做出来的，能拿走的就在这一行上拿，要比比上一版也从这一行进。',
    file: 'src/views/artifact/ArtifactVersionList.vue',
    component: ArtifactVersionList,
    needs: UI_T_R,
    args: { projectId: 'p-demo', downloading: '' },
    states: [
      {
        name: '交付过的每一版',
        note: '一份文件、一个网址、一次合并在同一条时间线上：最新那一版带「当前」，认过它的那一行写着是谁采纳的，做着它的那次对话能点回去。',
        props: { versions: ARTIFACT_VERSIONS },
        expect: 'feat: 把月度报告导成 PDF',
      },
      {
        name: '交出去的不是文件',
        note: '两版都没有文件可给，但说的不是同一句话：一次合并没有文件，交付物留存之前递的那一版是没留存 —— 分得开才不会误以为东西丢了。',
        props: { versions: ARTIFACT_VERSIONS_WITHOUT_FILE },
        expect: '交出去的是这次合并',
      },
      {
        name: '一版都没有',
        note: '一版都没交付过时不留一块空白：就地说一句「暂无交付」。',
        props: { versions: [] },
        expect: '暂无交付',
      },
      {
        name: '做久了，先摆最近几版',
        note: '版本多了先摆最近四版，剩下的收进一颗按钮，几点它就是剩下几条（这里要做 6 版，先收 2 条）。',
        props: { versions: ARTIFACT_VERSIONS_MANY },
        expect: '显示更早的 2 版',
      },
    ],
  },
  {
    id: 'team-profile',
    title: 'TeamProfile',
    about:
      '小队的对外一面：没加入的人按地址打开看到的就是这一页 —— 名字、介绍、成员数，和一颗「加入」；小队开着审批时，按下去交的是申请（带理由），等着管理员批。',
    file: 'src/views/teams/TeamProfile.vue',
    component: TeamProfile,
    needs: UI_T_R,
    args: { team: teamProfile(), join },
    states: [
      {
        name: '还没加入，小队要审批',
        note: '开着审批的小队：先说清加入要人批准，所以下面多一栏写申请理由，按下去交出去的是申请，不是直接成为成员。',
        props: { team: teamProfile({ joinStatus: 'none', joinApproval: true }) },
        expect: '申请加入',
      },
      {
        name: '加入不用审批',
        note: '小队开着门、不用审批：没有理由那一栏，按下去直接成为成员。',
        props: { team: teamProfile({ joinStatus: 'none', joinApproval: false }) },
        expect: '加入团队',
      },
      {
        name: '已经是成员',
        note: '已经在里面的人看到的是另一件事：一句「你已经在这个团队里」，和一颗进团队主页的按钮。',
        props: { team: teamProfile({ joinStatus: 'member' }) },
        expect: '进入团队',
      },
      {
        name: '申请交过了，等审批',
        note: '申请交出去之后停在原地等：这一格说的就是「已提交申请，等待团队管理员审批」，没有别的东西可按。',
        props: { team: teamProfile({ joinStatus: 'pending' }) },
        expect: '已提交申请，等待团队管理员审批',
      },
    ],
  },
  {
    id: 'space-review-notice',
    title: 'SpaceReviewNoticeView',
    about:
      '所有者打开自己那块还没过审的空间时看到的一屏：说清它在审核中还是被驳回了（驳回带原因），出去的路回空间列表 —— 「我的空间申请」在那里。',
    file: 'src/views/spaces/detail/SpaceReviewNoticeView.vue',
    component: SpaceReviewNoticeView,
    needs: UI_T_R,
    states: [
      {
        name: '待审核',
        note: '刚建好、还没人审：这里还发不了题，邀请码也还用不了。',
        props: { status: 'PENDING' },
        expect: '空间还在审核中',
      },
      {
        name: '被驳回，带原因',
        note: '审核人写了原因就照写，再说去哪里改了重交。',
        props: { status: 'REJECTED', reason: '空间名称没说清是哪门课' },
        expect: '空间名称没说清是哪门课',
      },
    ],
  },
]
