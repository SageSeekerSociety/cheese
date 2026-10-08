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
 * 的是真接口。这里登记的是拆出来的那几件：横条、退回那一块、「改动」页顶部那块、两件
 * 零件，以及两张只读的历史脸（闸门未过和闸门没跑成是同一张脸的两个分支，
 * `AcceptGateFace` 一条条目两格）。
 *
 * 这里的 `CatalogEntry` 是 type-only 引用：`catalog.ts` 反过来要 `ACCEPT_ENTRIES` 这个
 * 值，运行时不构成循环。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import {
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
  acceptHeadProps,
  AGENT_NAME,
} from './catalogFixtures'

import AcceptDeliveringFace from '@/components/accept/AcceptDeliveringFace.vue'
import AcceptDockBar from '@/components/accept/AcceptDockBar.vue'
import AcceptGateFace from '@/components/accept/AcceptGateFace.vue'
import AcceptNoteLine from '@/components/accept/AcceptNoteLine.vue'
import AcceptPrChecks from '@/components/accept/AcceptPrChecks.vue'
import AcceptRejectForm from '@/components/accept/AcceptRejectForm.vue'
import ChangesReviewHead from '@/components/accept/ChangesReviewHead.vue'

/** 只吃 vuetify 的（`v-chip` / `v-icon` / `v-card` / `v-btn`）。 */
const UI: CatalogNeed[] = ['vuetify']
/** 还有不写死在模板里的字（「审阅」「退回」，以及「改动」页顶部的各处提示）。 */
const UI_T: CatalogNeed[] = ['vuetify', 'i18n']

/** 闸门那两张只读的历史脸要的三样（嘴里那个「芝士」也是 props 进来的）。 */
const GATE_BASE = { agentName: AGENT_NAME, agentHandle: 'cheese' }

/** 横条上那几种话（`useAcceptCard.ts` 里那段 `bar` 算出来的形状，逐字相同）。 */
const BAR_PENDING = {
  icon: 'mdi-source-merge',
  color: 'success',
  // 等谁审阅。卡上写的就是这个人（`work.room.accept.waitingOn` 那句话的口径）。
  title: `待 @${ACCEPT_REVIEWERS.find((r) => r.user_handle === ACCEPT_ONE.reviewer_handle)?.name ?? ACCEPT_ONE.reviewer_handle} 审阅`,
  column: 'needs_you' as const,
}

const BAR_DELIVERING = { icon: 'mdi-history', color: 'warning', title: '已采纳，合并未完成' }

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
    about: '贴在输入框上方的那一条：现在在等什么，轮到人时就在这一条上退回或采纳。点状态那半句去「改动」页看详情。',
    file: 'src/components/accept/AcceptDockBar.vue',
    component: AcceptDockBar,
    needs: UI_T,
    states: [
      {
        name: '轮到人决定',
        note: '球在人手上（后端算的 who 是 human）才放两颗按钮。交的是什么、检查怎样都在「改动」页顶部，这里只说状态。',
        props: { ...BAR_PENDING, decide: true, acceptLabel: '采纳并完成任务' },
        expect: '采纳并完成任务',
      },
      {
        name: '检查还在跑',
        note: '这时点采纳也合不进去，要人做的事是等：只写合并态那个词，不放按钮。',
        props: { icon: 'mdi-source-merge', color: 'success', title: '检查进行中', column: 'delivering' },
        expect: '检查进行中',
      },
      {
        name: '采纳现在点不了',
        note: '轮到人了但合不进去（比如还差一个批准）：按钮灰着，为什么灰写在 title 里。',
        props: {
          ...BAR_PENDING,
          decide: true,
          acceptLabel: '采纳并完成任务',
          blockedTitle: '现在采纳不会合并：还需要 1 人批准',
        },
        expect: '退回',
      },
      {
        name: '手机上',
        note: '手机上对话和「改动」是两个页签：这一条只放「审阅」，决定在「改动」页底部那一条上。',
        props: { ...BAR_PENDING, reviewButton: true },
        expect: '审阅',
      },
      {
        name: '已采纳',
        note: '归档话题上那张已采纳的卡：存在的理由只有一个，给一次反悔留个入口。',
        props: { icon: 'mdi-check-circle-outline', color: 'success', title: '@王长鑫 已采纳', revoke: true },
        expect: '撤回采纳',
      },
      {
        name: '历史卡',
        note: '已采纳但合并没走完的老卡：点这一条在上面展开当年那张卡，由调用方画。',
        props: { ...BAR_DELIVERING, expandable: true, expanded: false },
        expect: BAR_DELIVERING.title,
      },
    ],
  },
  {
    id: 'accept-reject-form',
    title: 'AcceptRejectForm',
    about: '点了「退回」之后输入框的位置换成这一块：写给芝士的退回理由，可以不写。',
    file: 'src/components/accept/AcceptRejectForm.vue',
    component: AcceptRejectForm,
    needs: UI_T,
    states: [
      {
        name: '刚点开',
        note: '理由可以不写，空着也能退回。',
        props: { busy: false, note: '' },
        expectSelector: 'textarea[placeholder="退回理由（可选）"]',
      },
      {
        name: '正在退回',
        note: '`busy` 时两颗按钮都点不动，退回那颗转圈。',
        props: { busy: true, note: '待决提示的问题修复后再提交' },
        expect: '取消',
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
    id: 'changes-review-head',
    title: 'ChangesReviewHead',
    about:
      '「改动」页顶部：这次交付的标题、一排信号（检查、合并、批准）和审阅重点。改派、自动合并、仍要采纳、作废在「更多操作」里。',
    file: 'src/components/accept/ChangesReviewHead.vue',
    component: ChangesReviewHead,
    needs: UI_T,
    states: [
      {
        name: '可以采纳',
        note: '信号逐个从卡上的数据来：合并态是 clean，所以「可以合并」画绿勾。没有数据的信号不出现。',
        props: acceptHeadProps(),
        expect: 'docs: add a welcome note',
      },
      {
        name: '检查还没绿',
        note: '「仍要采纳」和「检查通过后自动合并」只在这一档出现，收在「更多操作」里。',
        props: acceptHeadProps({ card: ACCEPT_BLOCKED, autoMergeVisible: true, forceMergeVisible: true }),
        expect: 'docs: add a welcome note',
      },
      {
        name: '已经有人布防',
        note: '布防人由后端认定（`auto_merge.armed_by`），标题下面写一句由谁开启。',
        props: acceptHeadProps({ card: ACCEPT_BLOCKED, autoMergeVisible: true, autoMergeArmedBy: 'li' }),
        expect: '已开启',
      },
      {
        name: '要两个人批准',
        note: '主分支保护：`approvals_required > 1` 才有「批准」这个信号，点开看谁批过、自己批一票。',
        props: acceptHeadProps({ card: ACCEPT_APPROVALS }),
        expect: '1/2',
      },
      {
        name: '与主分支冲突',
        note: '冲突卡不画合并那个信号，下面一句说芝士在处理。',
        props: acceptHeadProps({ card: ACCEPT_CONFLICTED }),
        expect: '正在处理',
      },
    ],
  },
]
