/**
 * 验收卡（`TopicAcceptCard`）拆出来的那几件在预览站里的条目。
 *
 * 它们和别的条目没有两样（「这是什么 / 在哪儿 / 需要哪几样 / 看哪几格」，规矩见
 * `catalog.ts`），单独一份是因为 `catalog.ts` 已经八百多行，这么多条塞进去会顶到
 * `frontend/src` 那一千行的上限 —— 和 `catalogChat.ts`、`catalogQueue.ts` 同一个理由。
 * 数据见 `catalogFixtures.ts`。
 *
 * 为什么这些件值得一站：拆之前它们都是 1215 行 `TopicAcceptCard.vue` 里的几段模板
 * （#2143），想看其中任何一段都得先把整张卡拉起来 —— 而整张卡自己去接口取数，还得有
 * 假后端答得上来。拆开之后每一件都只吃 props、只往上发事件（`frontend_grade.py` 的 A
 * 级，几张脸也是：它们伸手拿的东西全在 `components/TopicAcceptCard.vue` 和
 * `composables/useAcceptCard.ts` 里），于是每一件都能单独摆在预览站里。
 *
 * 这里没有重复登记整张卡：它自己已经有一条（`catalog.ts` 的 `accept-card`），那条走
 * 的是真接口。这里登记的是拆出来的八件 —— 三件零件加五张脸里的四张（闸门未过和闸门
 * 没跑成是同一张脸的两个分支，`AcceptGateFace` 一条条目两格）。
 *
 * 这里的 `CatalogEntry` 是 type-only 引用：`catalog.ts` 反过来要 `ACCEPT_ENTRIES` 这个
 * 值，运行时不构成循环。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import {
  ACCEPT_ACCEPTED_ONE,
  ACCEPT_APPROVALS,
  ACCEPT_BLOCKED,
  ACCEPT_CHECKS,
  ACCEPT_CONFLICTED,
  ACCEPT_DELIVERING_ONE,
  ACCEPT_GATE_BLOCKED,
  ACCEPT_GATE_FAILED,
  ACCEPT_NOTE_ERROR,
  ACCEPT_NOTE_INFO,
  ACCEPT_ONE,
  ACCEPT_REVIEWERS,
  acceptChecks,
  acceptPendingProps,
  AGENT_NAME,
} from './catalogFixtures'

import AcceptDecidedFace from '@/components/accept/AcceptDecidedFace.vue'
import AcceptDeliveringFace from '@/components/accept/AcceptDeliveringFace.vue'
import AcceptDockBar from '@/components/accept/AcceptDockBar.vue'
import AcceptGateFace from '@/components/accept/AcceptGateFace.vue'
import AcceptNoteLine from '@/components/accept/AcceptNoteLine.vue'
import AcceptPendingFace from '@/components/accept/AcceptPendingFace.vue'
import AcceptPrChecks from '@/components/accept/AcceptPrChecks.vue'

/** 只吃 vuetify 的（`v-chip` / `v-icon` / `v-card` / `v-btn`）。 */
const UI: CatalogNeed[] = ['vuetify']
/** 还有不写死在模板里的字（「审阅」「展开详情」，以及待采纳那张脸上的各处提示）。 */
const UI_T: CatalogNeed[] = ['vuetify', 'i18n']

/** 闸门那两张只读的历史脸要的三样（嘴里那个「芝士」也是 props 进来的）。 */
const GATE_BASE = { agentName: AGENT_NAME, agentHandle: 'cheese' }

/** 横条上那四种话（`useAcceptCard.ts` 里那段 `bar` 算出来的形状，逐字相同）。 */
const BAR_PENDING = {
  icon: 'mdi-source-merge',
  color: 'success',
  // 交一次合并时，横条报的是这次改动自己的标题（卡上的 `change_subject`）——「《同一个
  // 名字》第 N 版」一行里说不出这次改了什么。
  title: ACCEPT_ONE.change_subject ?? '',
  // 等谁审阅。卡上写的就是这个人（`work.room.accept.waitingOn` 那句话的口径）。
  sub: `待 @${ACCEPT_REVIEWERS.find((r) => r.user_handle === ACCEPT_ONE.reviewer_handle)?.name ?? ACCEPT_ONE.reviewer_handle} 审阅`,
}

const BAR_DELIVERING = { icon: 'mdi-history', color: 'warning', title: '已采纳，合并未完成', sub: '' }

export const ACCEPT_ENTRIES: CatalogEntry[] = [
  {
    id: 'accept-pr-checks',
    title: 'AcceptPrChecks',
    about: '验收卡上那段 PR：编号、这一版是哪一次提交、和主分支冲不冲突、每一项检查跑到哪了。',
    file: 'src/components/accept/AcceptPrChecks.vue',
    component: AcceptPrChecks,
    needs: UI,
    states: [
      {
        name: '检查在跑',
        note: '没跑完的那一项写「进行中」。check 是不是跑完看的是 `status`，不是 `conclusion` —— GitHub 上跑着的 check 那个字段还是空的。',
        props: {
          card: ACCEPT_ONE,
          checks: acceptChecks([{ name: 'CI required', status: 'in_progress', conclusion: null }]),
        },
        expect: '进行中',
      },
      {
        name: '全绿',
        note: '跑完而且过了的那一项只有一个勾。这一段的起点是剧本第四步那份检查（`ACCEPT_CHECKS`，当时 `checks` 还是空的）。',
        props: {
          card: ACCEPT_ONE,
          checks: acceptChecks([{ name: 'CI required', status: 'completed', conclusion: 'success' }]),
        },
        expect: 'PR #1',
      },
      {
        name: '和主分支冲突',
        note: '冲突看的是 `mergeable`（false 才写），和检查跑没跑完是两件事：这里检查是绿的，照样有这一行。',
        props: {
          card: ACCEPT_ONE,
          checks: acceptChecks([{ name: 'CI required', status: 'completed', conclusion: 'success' }], {
            mergeable: false,
          }),
        },
        expect: '与主分支冲突',
      },
      {
        name: '交付中（写着是哪一版）',
        note: '交付中那张脸多传一个 `sha`（PR 的 head）：卡上要说得清「你采纳的是哪一次提交」，所以这里只截前 7 位。',
        props: {
          card: ACCEPT_ONE,
          checks: acceptChecks([{ name: 'CI required', status: 'in_progress', conclusion: null }]),
          sha: 'a91c3e0f7d',
        },
        expect: 'a91c3e0',
      },
    ],
  },
  {
    id: 'accept-note-line',
    title: 'AcceptNoteLine',
    about: '卡上那条 note：后端写的一句话（凭据坏了、PR 有新提交、GitHub 拒绝合并……），照原样念出来。',
    file: 'src/components/accept/AcceptNoteLine.vue',
    component: AcceptNoteLine,
    needs: UI,
    states: [
      {
        name: '停住了（error）',
        note: '「停住了」和后端下发的 `note_level` 是同一件事：红色加一个图标。这里画图标不是装饰 —— 颜色是唯一信号的话，色觉障碍和灰度截图上就什么都没有了。',
        props: { text: ACCEPT_NOTE_ERROR, tone: 'error' },
        expect: ACCEPT_NOTE_ERROR,
      },
      {
        name: '还在走（info）',
        note: '平台自己还在推进（这里这句是「PR 有新提交，已有的采纳批准被作废」）：退成次要色，不画图标。',
        props: { text: ACCEPT_NOTE_INFO, tone: 'info' },
        expect: ACCEPT_NOTE_INFO,
      },
    ],
  },
  {
    id: 'accept-dock-bar',
    title: 'AcceptDockBar',
    about: '贴在输入框上方的那一行横条：这是什么、等谁、去验收，点一下展开整张卡。',
    file: 'src/components/accept/AcceptDockBar.vue',
    component: AcceptDockBar,
    needs: UI_T,
    states: [
      {
        name: '收着（有一张待采纳的卡）',
        note: '展开的那一块长在这一条上面，不在它里面，所以「收着 / 摊开」只是一个 `expanded`：横条自己不知道卡里是什么。',
        props: { ...BAR_PENDING, expanded: false, canReview: true },
        expect: BAR_PENDING.title,
      },
      {
        name: '摊开',
        note: '同一个 `expanded` 的另一半：箭头翻过来，上面那一块由调用方画。',
        props: { ...BAR_PENDING, expanded: true, canReview: true },
        expect: BAR_PENDING.sub,
      },
      {
        name: '没有东西可以验收',
        note: '「去验收」那颗按钮只在真有卡要采纳的时候画（`canReview`）。这条横条也在报「已采纳但合并没走完」，那时候点进去没有东西可看，所以不画。',
        props: { ...BAR_DELIVERING, expanded: false, canReview: false },
        expect: BAR_DELIVERING.title,
      },
    ],
  },
  {
    id: 'accept-gate-face',
    title: 'AcceptGateFace',
    about: '机器闸门留下来的两张只读历史脸：检查未通过（代码红了）和检查未能执行（检查本身没跑起来）。',
    file: 'src/components/accept/AcceptGateFace.vue',
    component: AcceptGateFace,
    needs: ['vuetify', 'i18n'],
    states: [
      {
        name: '检查未通过，输出点开了',
        note: '闸门退役（#296）之后不会再有卡进这两个状态，但退役之前递的卡还在库里 —— 这张脸就是给它们留的。`gate_output` 是那次检查命令的输出尾巴，展开才念。',
        props: { card: ACCEPT_GATE_FAILED, open: true, ...GATE_BASE },
        expect: ACCEPT_GATE_FAILED.gate_output,
      },
      {
        name: '检查未通过，收着',
        note: '默认收着：这一格当年印出来的可能是一屏输出，卡上先只说「未通过」。',
        props: { card: ACCEPT_GATE_FAILED, open: false, ...GATE_BASE },
        expect: '查看检查输出',
      },
      {
        name: '检查没能跑起来',
        note: '和上面刻意分开的一张脸：这一张对代码没有结论，是需要人看一眼的状态，不是代码的问题 —— 所以两句话、两种措辞，不合成一句。',
        props: { card: ACCEPT_GATE_BLOCKED, open: true, ...GATE_BASE },
        expect: '检查未能运行',
      },
    ],
  },
  {
    id: 'accept-delivering-face',
    title: 'AcceptDeliveringFace',
    about: '「已采纳，但合并没走完」那张兜底脸：只读、不转圈，念一句后端写在卡上的故障，再挂上真 PR 和它的 CI。',
    file: 'src/components/accept/AcceptDeliveringFace.vue',
    component: AcceptDeliveringFace,
    needs: ['vuetify', 'i18n'],
    states: [
      {
        name: '只是没合完',
        note: '采纳即合并（#718）之后这个状态也不会再有新卡进来（存量已经迁回待采纳），所以这里一个动作都不给：它只说明卡停在哪。',
        props: { card: ACCEPT_DELIVERING_ONE, checks: ACCEPT_CHECKS, note: null },
        expect: '已采纳，但合并未完成',
      },
      {
        name: '后端还写了句故障',
        note: '后端写在卡上的 note 就在这里露头（`note_level` 说了是「停住了」还是「还在走」），文案本身是它下发的原话。',
        props: { card: ACCEPT_DELIVERING_ONE, checks: ACCEPT_CHECKS, note: { text: ACCEPT_NOTE_ERROR, tone: 'error' } },
        expect: ACCEPT_NOTE_ERROR,
      },
    ],
  },
  {
    id: 'accept-decided-face',
    title: 'AcceptDecidedFace',
    about: '归档话题上那张已采纳的卡：存在的理由只有一个 —— 给一次反悔留个入口。',
    file: 'src/components/accept/AcceptDecidedFace.vue',
    component: AcceptDecidedFace,
    needs: ['vuetify', 'i18n'],
    states: [
      {
        name: '可以撤回',
        note: '「谁采纳的」也在这张脸上：`decided_by` 是后端记下的那个 handle（人可能已经不在项目里，所以照样按 handle 画）。',
        props: { card: ACCEPT_ACCEPTED_ONE, busy: false },
        expect: '撤回采纳',
      },
      {
        name: '正在撤回',
        note: '`busy` 时那颗按钮转圈并禁用 —— 撤回是一个正在打的请求，人得看得出来（不然会连点）。',
        props: { card: ACCEPT_ACCEPTED_ONE, busy: true },
        expect: '已采纳',
      },
    ],
  },
  {
    id: 'accept-pending-face',
    title: 'AcceptPendingFace',
    about: '待采纳那张脸：这次交的是什么、在等谁、为什么还不能采纳，以及人在这里能做的每一件事。',
    file: 'src/components/accept/AcceptPendingFace.vue',
    component: AcceptPendingFace,
    needs: UI_T,
    states: [
      {
        name: '可以采纳',
        note: '干净的一档：合并态是 clean，按钮亮着。状态词（「可以合并」）和那个「该谁动」的圈都是后端算好、`lib/mergeState.ts` 翻出来的，这张脸自己一个都不推。',
        props: acceptPendingProps(),
        expect: 'docs: add a welcome note',
      },
      {
        name: '检查还没绿',
        note: '后端这一档会拒掉采纳，所以按钮灰着、灰的理由在 title 里，依据行念的是「检查进行中」；「仍要采纳」（人工放行）和「检查通过后自动合并」也只在 `blocked` / `behind` 这一档出现。',
        props: acceptPendingProps({
          card: ACCEPT_BLOCKED,
          blockedTitle: '现在采纳不会合并：检查进行中',
          autoMergeVisible: true,
        }),
        expect: '检查通过后自动合并',
      },
      {
        name: '已经有人布防',
        note: '布防人和时间由后端认定（`auto_merge.armed_by`），卡上只写「由谁开启」——同一个开关第二次进来要能解除，所以已经布防的开关一直可见。',
        props: acceptPendingProps({
          card: ACCEPT_BLOCKED,
          blockedTitle: '现在采纳不会合并：检查进行中',
          autoMergeVisible: true,
          autoMergeArmedBy: 'li',
        }),
        expect: '由',
      },
      {
        name: '要两个人批准',
        note: '主分支保护：`approvals_required > 1` 才有这一行。谁批过了念出来，没批的人在下面看见「批准」那颗按钮。',
        props: acceptPendingProps({ card: ACCEPT_APPROVALS }),
        expect: '1/2',
      },
      {
        name: '贴在输入框上方',
        note: '`docked` 时这一块长在横条底下，「审阅」那一颗留给横条，这里不再放第二颗 —— 同一个动作在同一屏上只出现一次。',
        props: acceptPendingProps({ docked: true }),
        expect: '退回',
      },
      {
        name: '与主分支冲突',
        note: '冲突卡不画状态词那一行（标题已经说了「芝士正在处理」），采纳那颗按钮换成「重新采纳」。',
        props: acceptPendingProps({ card: ACCEPT_CONFLICTED }),
        expect: '重新采纳',
      },
    ],
  },
]
