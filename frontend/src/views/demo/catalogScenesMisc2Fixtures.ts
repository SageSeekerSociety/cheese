/**
 * 第三批「拆出来的半张画面」（管理员看板告警、小队知识库、后台成员管理）在预览站里吃的
 * 数据。
 *
 * 形状照抄类型叶子里的声明（`types/spaces.ts` 的 `SpaceAnalyticsAlerts`、
 * `types/admin.ts` 的 `PlatformAdminsPayload` / `AdminCandidate`），少一个键多一个键都在
 * `vue-tsc` 那里当场红。知识库那几件复用 `catalogKnowledgeFixtures.ts` 的
 * `KNOWLEDGE_ROWS` / `KNOWLEDGE_TAGS`（网格和列表看的是同一份），所以这里只补告警与成员
 * 两块。
 *
 * 时间戳写死而不是 `Date.now()`：`relTime` 要的是稳定的那几格字，跟着当天变的话这一份就
 * 不叫「真会出现的形状」了。单独一份文件，是因为条目已经在 `catalogScenesMisc2.ts` 里
 * 顶到行数，数据再塞进去会过 `frontend/src` 那一千行的上限。
 */
import type { AdminCandidate, PlatformAdminsPayload, SpaceAnalyticsAlerts } from '@/types'

/** 「告警」那一格六个计数，各自不同，好让每一张卡都看得见自己那个数。 */
export const ANALYTICS_ALERTS: SpaceAnalyticsAlerts = {
  pendingTaskApprovalCount: 3,
  pendingParticipantApprovalCount: 5,
  pendingSubmissionReviewCount: 2,
  stalledTaskCount: 1,
  overdueUnreviewedSubmissionCount: 4,
  inactivePublisherCount: 0,
}

/**
 * 名单：根那两块各一行（一个正常账号、一个平台里没有的 agent 死权限），页面上加的一行
 * 带出处。四件事要看得见 —— 「昵称和 handle 是两个串」「没账号 / 没头像各有各的画法」
 * 「agent 挂在 who 列上」「删不掉那几行在根这一组」。
 */
export const MEMBERS_ROSTER: PlatformAdminsPayload = {
  root: [
    {
      handle: 'cheese',
      nickname: '林可',
      avatar_id: 3,
      has_account: true,
      registered_at: '2023-01-05T08:00:00Z',
      is_agent: false,
    },
    {
      handle: 'legacy-bot',
      nickname: null,
      avatar_id: null,
      has_account: false,
      registered_at: null,
      is_agent: true,
    },
  ],
  added: [
    {
      handle: 'alice',
      nickname: '爱丽丝',
      avatar_id: 2,
      has_account: true,
      registered_at: '2023-03-02T08:00:00Z',
      is_agent: false,
      added_by_handle: 'cheese',
      created_at: '2024-05-10T09:30:00Z',
    },
  ],
}

/** 加人框里那串候选：一个能加、一个已经是管理员（勾成灰掉并挂徽章）。 */
export const MEMBERS_CANDIDATES: AdminCandidate[] = [
  { handle: 'bob', nickname: '鲍勃', avatar_id: 5, already_admin: false },
  { handle: 'carol', nickname: '卡罗尔', avatar_id: null, already_admin: true },
]
