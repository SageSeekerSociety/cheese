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

/** 题目的附件。**真平台上题目还没有这一层** —— 最接近的两个原语是挂在「提交物
 *  要求」上的 `Attachment`（type/url/meta）和素材库的 `Material`（带 name 与
 *  download_count）。这里按后者的形状取字段：有名字、有大小、有下载次数，
 *  因为「发题时附一份材料、领取的人下下来用」本来就是那件事。 */
export interface TaskFile {
  name: string
  /** 字节。 */
  size: number
  kind: 'pdf' | 'image' | 'code' | 'archive' | 'doc'
  /** 已有多少人下过。 */
  downloads: number
}

/** 把字节数说成人话。 */
export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

export const FILE_ICON: Record<TaskFile['kind'], string> = {
  pdf: 'mdi-file-pdf-box',
  image: 'mdi-file-image-outline',
  code: 'mdi-file-code-outline',
  archive: 'mdi-folder-zip-outline',
  doc: 'mdi-file-document-outline',
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
  /** 讲解视频。真库 `Task.video_url`；详情页只把 B 站链接转成内嵌播放器。 */
  videoUrl?: string | null
  /** 这道题是怎么来的。从 PDF 生成的那批会写「PDF · 第 2 页」，手写的没有这一项。 */
  origin?: string
  /** 发题时附上的材料，领取者和审核者可以下载。可选。 */
  files?: TaskFile[]
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

const CURATED: BoardTask[] = [
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
    videoUrl: 'https://www.bilibili.com/video/BV1xx411c7mD',
    files: [
      { name: '并发领取测试脚手架.zip', size: 48 * 1024, kind: 'archive', downloads: 12 },
      { name: '接口约定.md', size: 6 * 1024, kind: 'doc', downloads: 21 },
    ],
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

// --- 批量造的题：页码条要真的能翻起来 -----------------------------------------
//
// 上面那 12 道是手写的，题名有意挑成「讲这块板自己的问题」（并发领取、能被重复
// 兑换的邀请码…），评审时一眼能认出。但一页 20 条、页码条要人点得动，12 道连一页
// 都填不满 —— 于是这里再批量造一批**形状相同、内容平淡**的题把板填厚：上板 52 道、
// 待审 24 道、驳回 6 道。
//
// 「待审 24 道」是刻意造的：开放发题之后审核队列积压，正是这次重设计要讲的场景，
// 所以审核页的页码条也得有东西可翻。驳回那 6 道写的是反例（自动通过、不分页、
// 广告位…），驳回理由顺手把「为什么不这么做」讲在界面上。
//
// 随机数用固定种子的 LCG，不用 Math.random —— 每次构建出来的必须是同一块板，
// 否则页码、看板数字每刷一次都变，截图和评审都没法对着看。

const seeded = (seed: number) => {
  let s = seed >>> 0
  return () => (s = (s * 1664525 + 1013904223) >>> 0) / 4_294_967_296
}

const BULK_TAGS: Record<string, string[]> = {
  算法与数据结构: ['算法', '复杂度', '数据结构', '动态规划', '图论'],
  系统与网络: ['并发', '网络', '存储', '协议', '操作系统'],
  前端与体验: ['交互', '可视化', '响应式', '无障碍', '动效'],
  数据与分析: ['统计', '指标', '数据清洗', '报表', '可视化'],
  安全: ['权限', '加密', '越权', '审计', '注入'],
  其他: ['产品', '治理', '文档', '协作', '流程'],
}

const BULK_SUMMARIES = [
  '按题目要求给出实现或方案，说清取舍，并交代你是怎么验证的。',
  '交付要能跑起来，同时给出你判断它正确的依据。',
  '先把口径和边界写清楚再动手；交付里要能看出为什么这么做。',
  '方案里请分开写「验证过的场景」和「没验证的部分」。',
  '给出一版能用的实现，附上你踩到的坑和绕过它的办法。',
  '结论一句话，依据要有可复核的证据。',
]

const PUB_TITLES = [
  '给题目列表加一个「即将截止」的提醒',
  '写一份题目板的使用手册',
  '把「领取」按钮做成有状态的',
  '给题目详情页加「相关题目」',
  '统计每道题的平均完成时间',
  '把板内搜索换成支持标签组合的',
  '给题目加「难度」并说明怎么定档',
  '设计一套题目模板，减少出题时的重复劳动',
  '截止前自动提醒还没提交的小队',
  '给「我的」页加一个进度条',
  '写个脚本把散落的题目描述统一格式',
  '领取时校验小队人数是否满足限制',
  '给审核加一条「批量通过」',
  '讨论区支持 @ 提及',
  '把题目导出成 Markdown',
  '题目附件支持拖拽上传',
  '给题目加草稿自动保存',
  '做一个题目标题查重',
  '领取记录支持限时撤销',
  '给每道题一个唯一的短链',
  '统计「领了但一直没动」的人并提醒',
  '把看板的 KPI 做成可点击的钻取',
  '给小队加队内讨论区',
  '题目通过后自动通知领取者',
  '支持给题目打星级评分',
  '把题目板接入日历订阅',
  '给题目加「先修要求」',
  '支持题目按学期归档',
  '做一个题目质量自检清单',
  '领取人数上限支持动态调整',
  '给题目加多语言描述',
  '支持从表格批量导入题目',
  '给审核队列加处理时长统计',
  '把题目详情页做成可分享的卡片',
  '支持给题目配一个示例提交',
  '统计每个分类的平均领取率',
  '给题目加「已满员」时的候补队列',
  '支持按出题人筛选题目',
  '记住用户自己的排序偏好',
  '题目描述里的链接做成可预览的',
  '支持给题目加参考实现',
  '做一个「本周新题」的订阅摘要',
  '给题目加结束后的复盘入口',
  '把题目关联到具体的课程章节',
  '给板内成员加技能标签',
  '支持把题目转成一份作业单',
  '给题目加领取冷却期',
  '统计题目之间的先后依赖',
  '给题目板加一个「紧急」标记',
  '按小队规模自动分组展示题目',
  '把题目描述的编辑器换成 Markdown',
  '给题目加一个版本历史',
]

const PENDING_TITLES = [
  '给题目列表加虚拟滚动',
  '做一个按历史领取的题目推荐',
  '支持题目跨板复制',
  '给审核加一个「需要补充材料」的状态',
  '把领取上限改成按小队计',
  '支持题目定时上板',
  '给题目加一个匿名反馈入口',
  '题目板的移动端手势操作',
  '支持把题目导出成 PDF 讲义',
  '给题目加一个「已解决」标记',
  '统计发布到第一次领取的延迟',
  '支持给题目配一套自动评测用例',
  '给题目加一个公开的讨论热榜',
  '做一个题目板的数据大屏',
  '支持按标签订阅题目',
  '给题目加一个延期申请流程',
  '支持被驳回的题一键重提',
  '给题目板加操作审计日志',
  '支持给题目配一个视频讲解',
  '做一个题目板的开放 API',
  '支持题目描述里的公式渲染',
  '给题目加一个「同类题」对比视图',
  '支持题目板之间的题目引用',
  '做一个题目的领取者名片视图',
]

const REJECTED: [string, string][] = [
  ['把题目板首页换成一屏大图', '首页是导航入口，一屏大图会把列表和筛选项藏起来，先不动。'],
  ['支持把题目转发到聊天工具', '转发会把题目带出板外，权限、截止和领取状态都会失真。'],
  ['给题目加一个「付费解锁」按钮', '题目必须对板内成员等价开放，不引入付费。'],
  ['支持题目自动通过（不审核）', '审核正是这次重设计要立的规则，不能自动通过。'],
  ['把全部题目一次性展开（不分页）', '题目会到几百道，不分页首页会先垮掉。'],
  ['给题目加一个全屏广告位', '板内不做广告位。'],
]

/** 批量题的领取名单：只写形状（多少人、什么状态分布），人数从名单数出来。 */
function bulkRoster(rand: () => number): Claimant[] {
  const fill = 2 + Math.floor(rand() * 22)
  const passed = Math.floor(fill * (0.25 + rand() * 0.35))
  const submitted = Math.floor((fill - passed) * (0.3 + rand() * 0.4))
  const working = fill - passed - submitted
  const teamSize = rand() < 0.35 ? 2 + Math.floor(rand() * 2) : undefined
  return makeRoster({
    named: [],
    fill,
    mix: [
      ['PASSED', passed],
      ['SUBMITTED', submitted],
      ['IN_PROGRESS', working + 3],
      ['REJECTED', 2],
    ],
    teamSize,
  })
}

function makeBulkTasks(): BoardTask[] {
  const rand = seeded(20260923)
  const publishers = [...Object.values(PEOPLE), ...FILLER]
  const out: BoardTask[] = []

  const pick = <T>(list: T[]) => list[Math.floor(rand() * list.length)]
  const tagsFor = (category: string) => {
    const pool = BULK_TAGS[category] ?? BULK_TAGS['其他']
    return [pool[Math.floor(rand() * pool.length)], pool[Math.floor(rand() * pool.length)]].filter(
      (t, i, a) => a.indexOf(t) === i
    )
  }
  /** 每 3 道里有 1 道挂在所有者名下 —— 让「我发布的」也有多页可翻（每页 20）。 */
  const publisherAt = (i: number) => (i % 3 === 0 ? PEOPLE.caisongyang : pick(publishers))

  PUB_TITLES.forEach((title, i) => {
    const category = CATEGORIES[i % CATEGORIES.length]
    const claims = bulkRoster(rand)
    const publishedDaysAgo = 1 + Math.floor(rand() * 26)
    // 五分之一左右已经截止（PUBLISHED + 过期 = 界面上那个「已截止」）。
    const overdue = rand() < 0.2
    const team = claims.some((c) => c.team) ? 2 + Math.floor(rand() * 3) : 1
    out.push(
      finalize({
        id: `b-pub-${i + 1}`,
        title,
        summary: BULK_SUMMARIES[i % BULK_SUMMARIES.length],
        category,
        tags: tagsFor(category),
        publisher: publisherAt(i),
        state: 'PUBLISHED',
        createdAt: daysBefore(publishedDaysAgo + 2),
        publishedAt: daysBefore(publishedDaysAgo),
        deadline: overdue ? daysBefore(1 + Math.floor(rand() * 5)) : daysAfter(1 + Math.floor(rand() * 30)),
        // 上限总比已领的多，否则卡片上会出现「已领 21 / 20」这种不可能的数。
        participantLimit: rand() < 0.3 ? null : claims.length + 5 + Math.floor(rand() * 25),
        minTeamSize: team,
        maxTeamSize: team,
        // 每 9 道里有一道带讲解视频，让「带视频的题」在列表里也看得见。
        videoUrl: i % 9 === 0 ? 'https://www.bilibili.com/video/BV1Q5411T7YB' : null,
        // 每 5 道里有一道带附件，列表上那个回形针标记才有东西可指。
        files:
          i % 5 === 0
            ? [
                {
                  name: `题目材料-${i + 1}.zip`,
                  size: (60 + i * 7) * 1024,
                  kind: 'archive' as const,
                  downloads: 3 + (i % 17),
                },
              ]
            : undefined,
        claims,
      })
    )
  })

  PENDING_TITLES.forEach((title, i) => {
    const category = CATEGORIES[i % CATEGORIES.length]
    // 待审的题还没有领取名单：它们没上板。
    out.push(
      finalize({
        id: `b-pend-${i + 1}`,
        title,
        summary: BULK_SUMMARIES[(i + 2) % BULK_SUMMARIES.length],
        category,
        tags: tagsFor(category),
        publisher: publisherAt(i + 3),
        state: 'PENDING',
        createdAt: daysBefore(Math.floor(rand() * 7)),
        deadline: daysAfter(3 + Math.floor(rand() * 30)),
        participantLimit: rand() < 0.5 ? null : 10 + Math.floor(rand() * 30),
        minTeamSize: 1,
        maxTeamSize: rand() < 0.5 ? 1 : 3,
        claims: [],
      })
    )
  })

  REJECTED.forEach(([title, reason], i) => {
    const category = CATEGORIES[(i + 4) % CATEGORIES.length]
    out.push(
      finalize({
        id: `b-rej-${i + 1}`,
        title,
        summary: BULK_SUMMARIES[(i + 4) % BULK_SUMMARIES.length],
        category,
        tags: tagsFor(category),
        publisher: pick([PEOPLE.maxiaoyu, PEOPLE.pengwenbo, PEOPLE.chiruotong]),
        state: 'REJECTED',
        rejectReason: reason,
        reviewedBy: i % 2 === 0 ? PEOPLE.caisongyang : PEOPLE.maxiaoyu,
        createdAt: daysBefore(6 + i),
        deadline: daysAfter(10 + i),
        participantLimit: null,
        minTeamSize: 1,
        maxTeamSize: 1,
        claims: [],
      })
    )
  })

  return out
}

export const TASKS: BoardTask[] = [...CURATED, ...makeBulkTasks()]

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

/** B 站链接 → 内嵌播放器地址。判据与真平台 `views/tasks/detail/Overview.vue` 的
 *  `videoEmbedUrl` 一致：只认 `bilibili.com/video/BV…`，其它域名一律 null（能存、不能播）。 */
export function videoEmbedUrl(url: string | null | undefined): string | null {
  const bv = url?.match(/bilibili\.com\/video\/(BV[\w]+)/)
  return bv ? `//player.bilibili.com/player.html?bvid=${bv[1]}&autoplay=0` : null
}

export function bilibiliBvid(url: string | null | undefined): string | null {
  const bv = url?.match(/bilibili\.com\/video\/(BV[\w]+)/)
  return bv ? bv[1] : null
}

/** 「从 PDF 生成题目」的假解析结果 —— 形状照着真接口
 *  `POST /tasks/publish/from-pdf/preview` 的返回捏（drafts + templateUsed + tokenUsed），
 *  另外把「抽出几张图」「原文件多少页」也摆出来：这两件事决定人要不要逐条改。 */
export const PDF_PREVIEW = {
  fileName: '计算机系统基础-第五次作业.pdf',
  pages: 14,
  templateUsed: '计算机系统基础 · 标准题模板',
  tokenUsed: 18742,
  imageCount: 3,
  /** 解析时顺手能当附件发出去的东西：原 PDF 本身，以及抽出来的那几张插图。 */
  files: [
    { name: '计算机系统基础-第五次作业.pdf', size: 2_411_724, kind: 'pdf' as const, downloads: 0 },
    { name: '第 2 页-图 1.png', size: 184_320, kind: 'image' as const, downloads: 0 },
    { name: '第 9 页-图 1.png', size: 226_918, kind: 'image' as const, downloads: 0 },
    { name: '第 9 页-图 2.png', size: 143_570, kind: 'image' as const, downloads: 0 },
  ],
  drafts: [
    {
      key: 'd1',
      title: '用 gdb 定位一次段错误',
      summary: '给定一段会崩的程序，用 gdb 找出崩在哪一行并说明寄存器状态。截图与命令都要交。',
      category: '系统与网络',
      images: 1,
      sourcePage: 2,
    },
    {
      key: 'd2',
      title: '手写一个最简内存分配器',
      summary: '实现 malloc / free 的最简版本，说明碎片是怎么产生的、你的策略付出了什么代价。',
      category: '系统与网络',
      images: 0,
      sourcePage: 5,
    },
    {
      key: 'd3',
      title: '论证「缓存行对齐」能带来多少加速',
      summary: '在两种访问模式下测同一段代码，给出数据、解释差异，并说明测量里哪些噪声没排掉。',
      category: '算法与数据结构',
      images: 2,
      sourcePage: 9,
    },
    {
      key: 'd4',
      title: '把给出的程序改成并发安全',
      summary: '题目附的代码有竞态，请修好并给出你判断它安全的依据。',
      category: '系统与网络',
      images: 0,
      sourcePage: 12,
    },
  ],
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

// --- 公告 --------------------------------------------------------------------

/** 一条空间公告。字段照着真平台取：真库里公告不是一个模型，而是 `space.announcements`
 *  这个 jsonb 数组里的一个元素，形状是 {title, content, createdAt, updatedAt,
 *  publisher}；`content` 是 tiptap 出来的 **HTML**，`publisher` 存的是发布时的昵称
 *  字符串，不是外键。这里把 publisher 写成 Person，是为了和原型别处一样显示假名。 */
export interface Announcement {
  id: string
  title: string
  /** 正文。真平台存的是富文本 HTML；原型里存纯文本，渲染时按空行分段、「- 」开头的
   *  一段当列表。落真代码时这一格换成真编辑器，渲染交给现成的 Viewer。 */
  content: string
  createdAt: string
  updatedAt: string
  publisher: Person
  /** 置顶。**真平台现在没有这一项** —— 公告只是一段数组，没有排序字段，谁也没法把一条
   *  一直摆在最前面。这一条是这次重设计**新增**的，落真代码要给元素加一个布尔字段。 */
  pinned: boolean
}

export const ANNOUNCEMENTS: Announcement[] = [
  {
    id: 'an-1',
    title: '从今天起，这块板上任何人都可以出题',
    content: `这块板不再只是几个人发题的地方。

- 任何成员都能出题，发出来进「待审核」，由所有者或管理员审过之后上板；
- 管理员自己出的题，自己就能审，不必等别人；
- 驳回会写明原因，作者改完可以重新提交。

出题不再是权限，是一件事 —— 谁有想让人做的东西，谁就可以发。`,
    createdAt: daysBefore(2, 9),
    updatedAt: daysBefore(2, 9),
    publisher: PEOPLE.caisongyang,
    pinned: true,
  },
  {
    id: 'an-2',
    title: '邀请码改成随时可调了',
    content: `邀请码挪到了板上方那块下拉里（所有者与管理员可见）。

- 可用人数和有效期就地可改，改完立刻生效，不用重建一个码；
- 也能随时撤销。

之前要改一个数就得建新码、作废旧码，成员手里的链接跟着失效一次。现在不用了。`,
    createdAt: daysBefore(5, 15),
    updatedAt: daysBefore(4, 11),
    publisher: PEOPLE.maxiaoyu,
    pinned: false,
  },
  {
    id: 'an-3',
    title: '本周五 18:00 之前，请把这三道题的末尾确认一下',
    content: `有三道题的截止时间落在本周五，出题的人请确认一下材料齐了没有：

- 给题目板写一个「领取人数」的并发安全实现
- 找出邀请码可以被重复兑换的路径
- 写一个能复现「领取超发」的最小用例

到点还没交材料，就按题面上写的默认期限处理。`,
    createdAt: daysBefore(1, 17),
    updatedAt: daysBefore(1, 17),
    publisher: PEOPLE.maxiaoyu,
    pinned: false,
  },
  {
    id: 'an-4',
    title: '数据看板对管理员开放了',
    content: `所有者与管理员现在能看到整块板的一屏数据：题目总数、领取与提交走势、题目构成、分类分布、最热的题、出题人排行。

普通成员看不到这一屏 —— 在自己的「我的」里能看到自己出的题和领的题，那就够了。`,
    createdAt: daysBefore(9, 10),
    updatedAt: daysBefore(9, 10),
    publisher: PEOPLE.caisongyang,
    pinned: false,
  },
]

/** 公告正文的分段：空行分段，「- 」开头的一段当列表。真平台这段由富文本编辑器直接
 *  产出 HTML，原型没有编辑器，所以在这里把纯文本折成块。 */
export function announcementBlocks(content: string): { type: 'p' | 'ul'; items: string[] }[] {
  const blocks: { type: 'p' | 'ul'; items: string[] }[] = []
  for (const chunk of content.split(/\n\s*\n/)) {
    const lines = chunk
      .split('\n')
      .map((l) => l.trim())
      .filter(Boolean)
    if (!lines.length) continue
    const bullets = lines.filter((l) => l.startsWith('- '))
    if (bullets.length === lines.length) {
      blocks.push({ type: 'ul', items: bullets.map((l) => l.slice(2)) })
    } else {
      blocks.push({ type: 'p', items: [lines.join(' ')] })
    }
  }
  return blocks
}
