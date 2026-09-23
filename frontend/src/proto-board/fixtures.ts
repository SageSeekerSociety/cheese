/**
 * 题目板重设计原型的假数据（临时，只被 `src/proto-board.ts` 引用）。
 *
 * 这一份是为了「把重设计讲清楚」而造的，不是从真库导出的快照。造数的三条纪律：
 *
 * 1. **字段照着真模型取**（`backend/app/domain/space/models.py`、`domain/task/models.py`）：
 *    题目上的 `participant_limit` / `min_team_size` / `max_team_size` / `deadline`、
 *    邀请码上的 `max_uses` / `use_count` / `expires_at` 都是真列名。原型说「保留原本
 *    就有的领取人数/小队限制」时，指的就是这几个。
 *
 * 2. **每个角色都要有能看的东西**：四份身份各自至少有一个「只有我能看到这一块」的
 *    理由（所有者看全板与授权、管理员看审核与看板、普通用户看自己的题、纯参与者
 *    看「我领的」）。数据里刻意留了：普通用户发的待审题（审核队列要有东西）、
 *    管理员自己发的题（自审那条规则要能演示）、一道已截止的题（预警要有东西）。
 *
 * 3. **凡是能算出来的数就不许手写第二遍**。这条是踩出来的：第一版给每道题手写了
 *    `submitted: 21 / passed: 18`，同时只列了 8 个领取者，于是看板顶上出现
 *    「领取 34、提交 108」—— 提交比领取还多，一眼假。现在 `submitted` / `passed`
 *    / `claimTrend` 全部**从领取名单派生**（`finalize()`），领取人数就是名单长度，
 *    提交和通过就是名单里各状态的人数。造数时只写名单和形状，数字自己长出来。
 */

/** 板内角色。与后端 `SpaceAdminRole`（OWNER=0 / ADMIN=1）同名，多出来的 MEMBER 是
 *  「不在管理员名单里」这件事的**显式名字** —— 真库至今靠缺行隐式表示。 */
export type Role = 'OWNER' | 'ADMIN' | 'MEMBER'

/** 题目状态。真库只用 `Task.approved`（0=APPROVED / 1=DISAPPROVED / 2=NONE=待审）
 *  一个整数装三态，这里拆成名字，因为界面上要显示的不是那个整数。 */
export type TaskState = 'PENDING' | 'PUBLISHED' | 'REJECTED' | 'CLOSED'

export interface Person {
  handle: string
  name: string
}

export interface Claimant {
  handle: string
  name: string
  at: string
  team?: string
  status: 'IN_PROGRESS' | 'SUBMITTED' | 'PASSED' | 'REJECTED'
}

export interface BoardTask {
  id: string
  title: string
  summary: string
  category: string
  tags: string[]
  publisher: Person
  state: TaskState
  rejectReason?: string
  reviewedBy?: Person
  createdAt: string
  publishedAt?: string
  deadline: string
  /** 领取人数上限。`null` = 不限。 */
  participantLimit: number | null
  /** 小队规模限制。`1/1` 就是「只能单人领」。 */
  minTeamSize: number
  maxTeamSize: number
  claims: Claimant[]
  /** 最近 12 天的累计领取。派生自 `claims`，见 `finalize()`。 */
  claimTrend: number[]
  /** 提交数 = 名单里除「进行中」以外的人数。派生。 */
  submitted: number
  /** 通过数 = 名单里「已通过」的人数。派生。 */
  passed: number
}

export interface InviteCode {
  code: string
  /** 可用人数上限；`null` = 不限。 */
  maxUses: number | null
  useCount: number
  /** 有效期终点；`null` = 永不过期。 */
  expiresAt: string | null
  createdBy: Person
  createdAt: string
  note: string
  revoked: boolean
}

export interface SpaceInfo {
  id: string
  name: string
  intro: string
  avatarSeed: string
  owner: Person
  admins: Person[]
  memberCount: number
  allowAnyonePublish: boolean
}

/** 时间一律相对「打开页面的这一刻」算：写死日期的话，过几天再打开全是「30 天前」，
 *  而那不是任何人会看到的界面（同反馈原型那份注释的理由）。 */
const BASE_MS = Date.now()

const daysBefore = (daysAgo: number, hour = 10) => {
  const d = new Date(BASE_MS - daysAgo * 86_400_000)
  d.setHours(hour, 0, 0, 0)
  return d.toISOString()
}
const daysAfter = (days: number, hour = 23) => {
  const d = new Date(BASE_MS + days * 86_400_000)
  d.setHours(hour, 0, 0, 0)
  return d.toISOString()
}

export const PEOPLE: Record<string, Person> = {
  caisongyang: { handle: 'caisongyang', name: '蔡松洋' },
  maxiaoyu: { handle: 'maxiaoyu', name: '马霄宇' },
  pengwenbo: { handle: 'pengwenbo', name: '彭文博' },
  chiruotong: { handle: 'chiruotong', name: '池若彤' },
  wangchangxin: { handle: 'wangchangxin', name: '符露夀' },
  ligan: { handle: 'ligan', name: '李甘' },
  n1ctheboy: { handle: 'n1ctheboy', name: '李甘-nictheboy' },
  andy: { handle: 'andy', name: 'andy' },
  andylizf: { handle: 'andylizf', name: 'andylizf' },
}

/** 名单之外的人。板有 37 个人，领取名单只写那九个熟面孔是不够的 —— 于是「参与人数 9」
 *  会和「成员 37」对不上。这些是凑数用的板内成员，名字照着同一批人取。 */
const FILLER: Person[] = [
  { handle: 'zhouyi', name: '周奕' },
  { handle: 'sunqi', name: '孙祺' },
  { handle: 'wuhao', name: '吴昊' },
  { handle: 'zhengshuang', name: '郑爽' },
  { handle: 'fengyu', name: '冯雨' },
  { handle: 'chenmo', name: '陈默' },
  { handle: 'xujing', name: '徐婧' },
  { handle: 'hanlei', name: '韩磊' },
  { handle: 'tangwei', name: '唐薇' },
  { handle: 'luochen', name: '罗晨' },
  { handle: 'hejiani', name: '何佳妮' },
  { handle: 'dengchao', name: '邓超' },
  { handle: 'yaoxuan', name: '姚璇' },
  { handle: 'songyang', name: '宋洋' },
  { handle: 'caoyu', name: '曹宇' },
  { handle: 'panlin', name: '潘琳' },
  { handle: 'qiuyi', name: '邱亦' },
  { handle: 'yinwei', name: '殷维' },
  { handle: 'luxin', name: '陆欣' },
  { handle: 'baijun', name: '白珺' },
]

/** 四份可切换的身份。所有者与管理员都安排成「出过题的人」，好让「自己发的题自己审」
 *  这条规则能在界面上当场演示。 */
export const IDENTITIES: { role: Role; me: Person; blurb: string }[] = [
  { role: 'OWNER', me: PEOPLE.caisongyang, blurb: '所有者 · 可管理成员、审核、看全板数据' },
  { role: 'ADMIN', me: PEOPLE.maxiaoyu, blurb: '管理员 · 可审核、看全板数据，不能改成员角色' },
  { role: 'MEMBER', me: PEOPLE.pengwenbo, blurb: '普通用户 · 发过题，看不到任何汇总看板' },
  { role: 'MEMBER', me: PEOPLE.chiruotong, blurb: '普通用户 · 只领过题，没发过题' },
]

export const SPACE: SpaceInfo = {
  id: 'sp-demo',
  name: '测试题目板',
  intro: '一块用来试新流程的题目板：任何人都能出题，管理员审过就能领。',
  avatarSeed: 'test-board',
  owner: PEOPLE.caisongyang,
  admins: [PEOPLE.maxiaoyu],
  memberCount: 37,
  allowAnyonePublish: true,
}

export const CATEGORIES = ['算法与数据结构', '系统与网络', '前端与体验', '数据与分析', '安全', '其他']

const TEAMS = ['阿尔法小队', '贝塔小队', '伽马小队', '德尔塔小队', '厄普西隆小队']

/**
 * 造一份领取名单：把指定的几位写死（名字和状态要能被人认出来），剩下的按需要补齐。
 *
 * `statusMix` 是补齐那部分的分布，形如 `['PASSED', 6]` 这样的成对值。写死的那几位
 * 不计入分布，但计入总数 —— 于是「通过 13 人」这种数最后是从名单数出来的，不是编的。
 */
function makeRoster(spec: {
  named: [string, number, Claimant['status'], string?][]
  fill: number
  mix: [Claimant['status'], number][]
  /** 小队题的取法：每 `teamSize` 人一组，组名从 `TEAMS` 里顺着拿。 */
  teamSize?: number
}): Claimant[] {
  const out: Claimant[] = spec.named.map(([handle, daysAgo, status, team]) => ({
    handle,
    name: PEOPLE[handle]?.name ?? handle,
    at: daysBefore(daysAgo, 9 + (daysAgo % 8)),
    status,
    team,
  }))

  const pool = FILLER.filter((p) => !out.some((c) => c.handle === p.handle))
  const queue: Claimant['status'][] = []
  for (const [status, n] of spec.mix) for (let i = 0; i < n; i++) queue.push(status)

  for (let i = 0; i < spec.fill && i < pool.length && i < queue.length; i++) {
    const person = pool[i]
    const status = queue[i]
    // 领取时间铺在前面 12 天里，越靠后领的人越晚 —— 这样走势图是单调上升的。
    const daysAgo = Math.max(0, Math.round(11 - (i / Math.max(1, spec.fill - 1)) * 10))
    const team = spec.teamSize ? TEAMS[Math.floor(i / spec.teamSize) % TEAMS.length] : undefined
    out.push({ handle: person.handle, name: person.name, at: daysBefore(daysAgo, 9 + (i % 8)), status, team })
  }

  out.sort((a, b) => new Date(a.at).getTime() - new Date(b.at).getTime())
  return out
}

/** 把 `claims` 折算成 `claimTrend` / `submitted` / `passed`。
 *
 *  走势按领取时间累加，压到 12 个桶里：最后一个桶必然等于领取总数，而看板上的
 *  「领取 N 人」用的也是领取总数 —— 两处不会打架。 */
function finalize(task: Omit<BoardTask, 'claimTrend' | 'submitted' | 'passed'>): BoardTask {
  const submitted = task.claims.filter((c) => c.status !== 'IN_PROGRESS').length
  const passed = task.claims.filter((c) => c.status === 'PASSED').length

  const start = BASE_MS - 11 * 86_400_000
  const buckets = new Array(12).fill(0)
  for (const c of task.claims) {
    const day = Math.floor((new Date(c.at).getTime() - start) / 86_400_000)
    buckets[Math.max(0, Math.min(11, 11 - day))] += 1
  }
  // 有些题是更早发出去的，名单只覆盖近 12 天里的一批；那样也照样从这 12 天开始算。
  let run = 0
  const claimTrend = (task.claims.length ? buckets : []).map((n) => (run += n))

  return { ...task, submitted, passed, claimTrend }
}

export const TASKS: BoardTask[] = [
  finalize({
    id: 't1',
    title: '给题目板写一个「领取人数」的并发安全实现',
    summary: '同一道题被很多人同时点「领取」时，不能超发。请实现并给出你判断并发安全的依据（测试、推理或两者都行）。',
    category: '系统与网络',
    tags: ['并发', '数据库', '后端'],
    publisher: PEOPLE.caisongyang,
    state: 'PUBLISHED',
    createdAt: daysBefore(20),
    publishedAt: daysBefore(20),
    deadline: daysAfter(9),
    participantLimit: 30,
    minTeamSize: 1,
    maxTeamSize: 1,
    claims: makeRoster({
      named: [
        ['pengwenbo', 10, 'PASSED'],
        ['chiruotong', 9, 'PASSED'],
        ['wangchangxin', 8, 'SUBMITTED'],
        ['ligan', 7, 'PASSED'],
        ['n1ctheboy', 5, 'REJECTED'],
      ],
      fill: 13,
      mix: [
        ['PASSED', 8],
        ['SUBMITTED', 3],
        ['IN_PROGRESS', 2],
      ],
    }),
  }),
  finalize({
    id: 't2',
    title: '把「我发布的题目」这一页做成能看出题目冷热的看板',
    summary: '出题人想知道：我的题有多少人领了、多少人在做、卡在哪儿。请设计并实现这一页。',
    category: '前端与体验',
    tags: ['可视化', '前端', '设计'],
    publisher: PEOPLE.pengwenbo,
    state: 'PUBLISHED',
    createdAt: daysBefore(16),
    publishedAt: daysBefore(15),
    deadline: daysAfter(5),
    participantLimit: 20,
    minTeamSize: 2,
    maxTeamSize: 4,
    claims: makeRoster({
      named: [
        ['chiruotong', 12, 'PASSED', '阿尔法小队'],
        ['wangchangxin', 12, 'PASSED', '阿尔法小队'],
        ['ligan', 9, 'SUBMITTED', '贝塔小队'],
        ['n1ctheboy', 9, 'SUBMITTED', '贝塔小队'],
        ['andy', 6, 'IN_PROGRESS', '伽马小队'],
        ['andylizf', 6, 'REJECTED', '伽马小队'],
      ],
      fill: 6,
      mix: [
        ['PASSED', 2],
        ['SUBMITTED', 2],
        ['IN_PROGRESS', 2],
      ],
      teamSize: 2,
    }),
  }),
  finalize({
    id: 't3',
    title: '设计一份「题目板数据看板」的信息层级',
    summary: '管理员打开看板最先要看什么？请给出一份信息层级与理由，做成可点的原型更好。',
    category: '数据与分析',
    tags: ['可视化', '信息架构'],
    publisher: PEOPLE.maxiaoyu,
    state: 'PUBLISHED',
    createdAt: daysBefore(12),
    publishedAt: daysBefore(12),
    deadline: daysAfter(12),
    participantLimit: null,
    minTeamSize: 1,
    maxTeamSize: 3,
    claims: makeRoster({
      named: [
        ['chiruotong', 8, 'PASSED'],
        ['pengwenbo', 7, 'SUBMITTED'],
        ['ligan', 4, 'IN_PROGRESS'],
      ],
      fill: 12,
      mix: [
        ['PASSED', 6],
        ['SUBMITTED', 3],
        ['IN_PROGRESS', 3],
      ],
    }),
  }),
  finalize({
    id: 't4',
    title: '找出邀请码可以被重复兑换的路径',
    summary: '背景：邀请码有次数上限。请找出上限在并发下失效的路径，并给出修法。',
    category: '安全',
    tags: ['安全', '并发'],
    publisher: PEOPLE.wangchangxin,
    state: 'PUBLISHED',
    createdAt: daysBefore(9),
    publishedAt: daysBefore(8),
    deadline: daysAfter(-2),
    participantLimit: 15,
    minTeamSize: 1,
    maxTeamSize: 1,
    claims: makeRoster({
      named: [
        ['andy', 6, 'PASSED'],
        ['andylizf', 5, 'PASSED'],
        ['n1ctheboy', 4, 'SUBMITTED'],
        ['ligan', 2, 'IN_PROGRESS'],
      ],
      fill: 11,
      mix: [
        ['PASSED', 5],
        ['SUBMITTED', 3],
        ['REJECTED', 1],
        ['IN_PROGRESS', 2],
      ],
    }),
  }),
  finalize({
    id: 't5',
    title: '为「小队领取」设计一个不会散伙的分组流程',
    summary: '小队规模有上下限。请设计报名到成组的过程，说明怎么处理「差一个人」的情况。',
    category: '其他',
    tags: ['产品', '分组'],
    publisher: PEOPLE.pengwenbo,
    state: 'PENDING',
    createdAt: daysBefore(2),
    deadline: daysAfter(20),
    participantLimit: 24,
    minTeamSize: 3,
    maxTeamSize: 5,
    claims: [],
  }),
  finalize({
    id: 't6',
    title: '写一个能复现「领取超发」的最小用例',
    summary: '接上一道并发题：把你复现出来的两步贴出来，要求别人照着跑能得到同样结果。',
    category: '系统与网络',
    tags: ['并发', '测试'],
    publisher: PEOPLE.chiruotong,
    state: 'PENDING',
    createdAt: daysBefore(1),
    deadline: daysAfter(18),
    participantLimit: 10,
    minTeamSize: 1,
    maxTeamSize: 2,
    claims: [],
  }),
  finalize({
    id: 't7',
    title: '把题目板首页的筛选做成可以分享的链接',
    summary: '筛完分类和标签之后，地址栏要能直接发给别人打开同一份结果。',
    category: '前端与体验',
    tags: ['前端', '路由'],
    publisher: PEOPLE.ligan,
    state: 'PENDING',
    createdAt: daysBefore(0, 9),
    deadline: daysAfter(21),
    participantLimit: null,
    minTeamSize: 1,
    maxTeamSize: 1,
    claims: [],
  }),
  finalize({
    id: 't8',
    title: '统计每道题的「领取后放弃率」',
    summary: '想知道有多少人领了但一直没动。请给出定义、口径和一个可复现的算法。',
    category: '数据与分析',
    tags: ['数据分析', '指标'],
    publisher: PEOPLE.maxiaoyu,
    state: 'REJECTED',
    rejectReason: '口径没写清：先定义「放弃」是按天数算还是按有没有提交算，否则算出来的数没法比。',
    reviewedBy: PEOPLE.caisongyang,
    createdAt: daysBefore(6),
    deadline: daysAfter(14),
    participantLimit: null,
    minTeamSize: 1,
    maxTeamSize: 1,
    claims: [],
  }),
  finalize({
    id: 't9',
    title: '把题目详情页的「领取」按钮做成有状态的',
    summary: '已领 / 已提交 / 已通过，按钮要说清现在是什么状态、下一步做什么。',
    category: '前端与体验',
    tags: ['前端', '交互'],
    publisher: PEOPLE.pengwenbo,
    state: 'PUBLISHED',
    createdAt: daysBefore(24),
    publishedAt: daysBefore(24),
    deadline: daysAfter(-6),
    participantLimit: 40,
    minTeamSize: 1,
    maxTeamSize: 1,
    claims: makeRoster({
      named: [
        ['chiruotong', 11, 'PASSED'],
        ['wangchangxin', 10, 'PASSED'],
        ['ligan', 9, 'PASSED'],
        ['andy', 7, 'PASSED'],
      ],
      fill: 19,
      mix: [
        ['PASSED', 13],
        ['SUBMITTED', 3],
        ['REJECTED', 3],
      ],
    }),
  }),
  finalize({
    id: 't10',
    title: '给题目列表加一个「最热」排序，并说明热度怎么算',
    summary: '热度不能只看领取数，否则老题永远压着新题。',
    category: '算法与数据结构',
    tags: ['算法', '排序'],
    publisher: PEOPLE.n1ctheboy,
    state: 'PUBLISHED',
    createdAt: daysBefore(14),
    publishedAt: daysBefore(14),
    deadline: daysAfter(3),
    participantLimit: 25,
    minTeamSize: 1,
    maxTeamSize: 2,
    claims: makeRoster({
      named: [
        ['chiruotong', 10, 'SUBMITTED'],
        ['pengwenbo', 8, 'PASSED'],
        ['andy', 5, 'IN_PROGRESS'],
      ],
      fill: 11,
      mix: [
        ['PASSED', 5],
        ['SUBMITTED', 3],
        ['IN_PROGRESS', 3],
      ],
    }),
  }),
  finalize({
    id: 't11',
    title: '「任何人可发题」之后，怎么防止刷题',
    summary: '开放发题会带来重复与灌水。请给出你支持的约束，并说明代价。',
    category: '其他',
    tags: ['产品', '治理'],
    publisher: PEOPLE.andy,
    state: 'PUBLISHED',
    createdAt: daysBefore(4),
    publishedAt: daysBefore(3),
    deadline: daysAfter(16),
    participantLimit: null,
    minTeamSize: 1,
    maxTeamSize: 1,
    claims: makeRoster({
      named: [
        ['chiruotong', 3, 'SUBMITTED'],
        ['ligan', 2, 'IN_PROGRESS'],
      ],
      fill: 7,
      mix: [
        ['PASSED', 2],
        ['SUBMITTED', 3],
        ['IN_PROGRESS', 2],
      ],
    }),
  }),
  finalize({
    id: 't12',
    title: '把板内的公告与讨论接进题目详情',
    summary: '出题人改了口径要能通知到已经领取的人。',
    category: '前端与体验',
    tags: ['通知', '讨论'],
    publisher: PEOPLE.maxiaoyu,
    state: 'PUBLISHED',
    createdAt: daysBefore(11),
    publishedAt: daysBefore(10),
    deadline: daysAfter(7),
    participantLimit: null,
    minTeamSize: 1,
    maxTeamSize: 1,
    claims: makeRoster({
      named: [
        ['pengwenbo', 6, 'PASSED'],
        ['wangchangxin', 5, 'IN_PROGRESS'],
        ['andylizf', 2, 'SUBMITTED'],
      ],
      fill: 9,
      mix: [
        ['PASSED', 4],
        ['SUBMITTED', 2],
        ['REJECTED', 1],
        ['IN_PROGRESS', 2],
      ],
    }),
  }),
]

export const INITIAL_CODES: InviteCode[] = [
  {
    code: 'BOARD-2K9F',
    maxUses: 50,
    useCount: 24,
    expiresAt: daysAfter(11),
    createdBy: PEOPLE.caisongyang,
    createdAt: daysBefore(30),
    note: '默认码',
    revoked: false,
  },
  {
    code: 'SPRINT-OCT',
    maxUses: 20,
    useCount: 4,
    expiresAt: daysAfter(3),
    createdBy: PEOPLE.maxiaoyu,
    createdAt: daysBefore(7),
    note: '十月这批同学',
    revoked: false,
  },
  {
    code: 'OPEN-DOOR',
    maxUses: null,
    useCount: 9,
    expiresAt: null,
    createdBy: PEOPLE.caisongyang,
    createdAt: daysBefore(60),
    note: '不限人数的公开码（现在想收掉）',
    revoked: false,
  },
]

// --- 汇总数字（看板与预警用的那一层）----------------------------------------
//
// 领取、提交、通过、参与人数、分类分布、排行榜**全部从 TASKS 算**（见 store.ts 的
// `kpis` / `claimRanking` 等），不再在这里写第二遍 —— 上面第 3 条纪律就是为这个立的。
//
// 留在这里的是**算不出来的那几项**：要另造一条基线才有的同比、以及不来自题目表的
// 成员数。显式写死，免得组件里偷偷算出一个看着像真事的数。

export const SUMMARY = {
  /** 上周同期的领取数，给 KPI 上的同比一个基线。 */
  claimsLastWeek: 96,
  /** 本周新增成员。 */
  newMembersThisWeek: 6,
  /** 近 12 天的活跃人数（当周有领取或有提交的人次）。 */
  activeTrend: [12, 15, 19, 22, 26, 29, 31, 33, 34, 34, 35, 36],
  /** 近 12 天的提交人次。 */
  submitTrend: [2, 4, 7, 9, 13, 16, 19, 22, 24, 26, 27, 28],
}

/** 近 12 天的日期标签，走势图横轴用。 */
export const DAY_LABELS: string[] = Array.from({ length: 12 }, (_, i) => {
  const d = new Date(BASE_MS - (11 - i) * 86_400_000)
  return `${d.getMonth() + 1}/${d.getDate()}`
})

export const ROLE_LABEL: Record<Role, string> = {
  OWNER: '所有者',
  ADMIN: '管理员',
  MEMBER: '成员',
}

export const STATE_LABEL: Record<TaskState, string> = {
  PENDING: '待审核',
  PUBLISHED: '已上板',
  REJECTED: '已驳回',
  CLOSED: '已截止',
}

export const CLAIM_LABEL: Record<Claimant['status'], string> = {
  IN_PROGRESS: '进行中',
  SUBMITTED: '已提交',
  PASSED: '已通过',
  REJECTED: '未通过',
}

/** 板上现在真正可领的题：审过了，且没到截止日。 */
export function isOpen(task: BoardTask): boolean {
  return task.state === 'PUBLISHED' && new Date(task.deadline).getTime() > BASE_MS
}

export function deadlineText(task: BoardTask): string {
  const ms = new Date(task.deadline).getTime() - BASE_MS
  const days = Math.ceil(ms / 86_400_000)
  if (days < 0) return `已截止 ${-days} 天`
  if (days === 0) return '今天截止'
  return `${days} 天后截止`
}
