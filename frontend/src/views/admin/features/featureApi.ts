// 功能数据（`/admin/feature-stats`）这一片的 HTTP 层：目录一条、按 id 取一份报告。
//
// 为什么单独一个文件：`src/api.ts` 早就过了 1000 行的上限，按
// `.claude/rules/architecture.md` 只许变小，不许再长。这一片在服务端是
// `app/domain/feature_stats/` 一块自己的东西，前端就跟着它自己的目录走 —— 加一个
// 功能时不必再去动那个已经数不清有多少个理由要改的文件。
import { request } from '@/api'

/* ---- 功能数据 (`/admin/feature-stats`) ----
 *
 * 后台有一类页面不是「一张板子」：它回答的是**某一个功能**怎么样，而每个功能的
 * 问题都不一样 —— 文档站要问访客与提问，别的功能可能是别的。所以服务端给的是一条
 * 目录 + 一条按 id 取数的接口，页面的形状由那个功能自己定（`app/domain/feature_stats`）。
 *
 * **目录里没有数字**，这是它的设计而不是还没做完：目录是一扇门，把各功能页上的
 * 数搬到门口就是第二份要跟着改的地方。目录页只列「有哪些功能、每个是什么」。
 *
 * `days` 只允许三档（和看板的 `StatsDays` 同一个理由）：切换器上就三个位置，而
 * 「窗口」这一档该进 URL/状态，不该在每个调用点各自传一个字面量。
 */

/** 目录里的一行。**没有 `view` / 路由 / 组件名这一栏**：服务端只知道有哪些功能，
 *  每个功能画成什么样子是前端注册表（`views/admin/features/registry.ts`）按 `id` 查的。
 *  让服务端回一个组件路径，等于把半个前端写进 Python 字符串里，改个文件名就烂。 */
export interface FeatureCatalogueEntry {
  id: string
  title: string
  summary: string
}

/** 「访客」这一格：`value` 是全部，`logged_in` 是其中登录的那些。两个数一起给而不是
 *  只给比例 —— 分母不写出来，「12 人」和「12%」长得一样。 */
export interface FeatureNumbers {
  visitors: { value: number; logged_in: number }
  askers: { value: number; share: number | null }
  questions: { value: number; per_asker: number | null }
  answer_rate: { value: number | null; answered: number; total: number }
  cost: {
    usd: number | null
    per_question: number | null
    /** `estimated` = 按网关价目表算的；`unavailable` = 读不到价目表，`usd` 是 null。 */
    source: 'estimated' | 'unavailable'
    unpriced_tokens: number
  }
}

/** 一问一答的形状：token 与耗时各一组描述统计（同一套字段名，`null` = 这个窗口里
 *  一条都没有）。 */
export interface FeatureDistribution {
  count: number
  avg: number | null
  median: number | null
  min: number | null
  p90: number | null
  max: number | null
}

export interface FeatureHistogramBin {
  from: number
  to: number
  count: number
}

export interface DocsAssistantTrendPoint {
  date: string
  visitors: number
  askers: number
  questions: number
}

/** 答不上来的那一张表。**没有「谁问的」这一列** —— 服务端也不发。 */
export interface UnansweredQuestion {
  question: string
  page: string | null
  count: number
}

export interface DocsAssistantReport {
  id: string
  title: string
  summary: string
  days: number
  start: string
  end: string
  numbers: FeatureNumbers
  trend: DocsAssistantTrendPoint[]
  tokens: FeatureDistribution & { histogram: FeatureHistogramBin[] }
  latency: FeatureDistribution
  outcomes: { answered: number; no_match: number; failed: number; total: number }
  unanswered: UnansweredQuestion[]
}

/** 智能命名那一页的形状（`/admin/feature-stats/topic-naming`）。
 *
 *  两个来源，都不新增埋点：钱来自网关那把**命名专用密钥**（命名跑在后台，不在谁的
 *  回合里），动作来自 `topic_titles` —— 标题每次真的被改都会在那里留一行。
 *
 *  **网关那几个数可以是 `null`**：读不到网关时它们是「没读到」，页面画长破折号，不画
 *  0（0 读作「这个窗口没花钱」，是另一个意思）。`cost.source` 就是这一格读到了没有。
 *  库里那几个数永远是真的。 */
export interface TopicNamingNumbers {
  /** `success_rate` 是网关的口径（请求打到模型并回来了），不是「命名成功率」——模型
   *  回了个不能用的答案在网关眼里也是成功。一个请求都没有时它是 `null`。 */
  calls: { value: number | null; failed: number | null; success_rate: number | null }
  tokens: {
    value: number | null
    prompt: number | null
    completion: number | null
    cache_read: number | null
  }
  cost: {
    usd: number | null
    /** `gateway` = 网关自己记的账；`no-key` = 网关答了话、上面没有那把命名密钥；
     *  `unavailable` = 读不到网关。后两种上面那几个数都是 null：一个是「密钥不
     *  存在」，一个是「没读到」，两句得分开说，都不能画成 0。 */
    source: 'gateway' | 'no-key' | 'unavailable'
    /** 那把密钥的额度与它自己记的花费：额度用完网关就会拒，命名会静默停下。
     *  `key_spend_usd` 跟的是**网关的额度周期（`budget_duration`），不是这一页选
     *  的窗口**，所以它和周期一起画 —— dev 上它比 7 天窗口的花费小一个量级。 */
    budget_usd: number | null
    budget_duration: string | null
    key_spend_usd: number | null
  }
  /** 平台自动写的标题，按阶段分（首次 / 校准 / 跟随）。 */
  renames: { value: number; name: number; calibrate: number; follow: number }
  /** 人写的标题，按原因分（改标题 / 撤销自动改名）。`value` 就是这两个格子的和：
   *  认不出的原因不进任何一格，也不进总数。 */
  person_edits: { value: number; rename: number; undo: number }
  /** `named` = 窗口里被自动命名过的房间数（比例的分母）；没有分母时 `share` 是 null。 */
  overridden: { value: number; named: number; share: number | null }
}

export interface TopicNamingTrendPoint {
  date: string
  /** 那天平台自动写了几个标题。 */
  auto: number
  /** 那天人改了几个标题。 */
  person: number
}

export interface TopicNamingReport {
  id: string
  title: string
  summary: string
  days: number
  start: string
  end: string
  numbers: TopicNamingNumbers
  trend: TopicNamingTrendPoint[]
}

/** 记忆那一页的形状（`/admin/feature-stats/memory`）。
 *
 *  它回答的是「一个项目的记忆树长成了什么样」——写了多少条、索引撑没撑破注入预算、有
 *  没有 orphan / dangling / 超长正文、整理跑到哪了、正文被读过几次。
 *
 *  **只数数，不读内容**：`name` 是项目名（不是记忆），其余全是计数。私人记忆按人聚合成
 *  `{entries, owners}`，报告里没有文件名、没有正文——这一列的口径见后端
 *  `feature_stats/features/memory.py` 的文件头。 */
export interface MemoryIndexShape {
  /** 索引行数/字节数。**没有索引文件时是 `null` 而不是 0**——「没有索引」和「索引是
   *  空的」是两句话。`over_*` 是撑破注入预算（200 行 / 25KB）的那两个布尔位。 */
  lines: number | null
  bytes: number | null
  over_lines: boolean
  over_bytes: boolean
}

export interface MemoryDreamShape {
  /** 上一次**成功结束**的时刻；从没成功过是 `null`。 */
  last_completed_at: string | null
  completed: number
  failed: number
  refused: number
  running: number
  /** `running` 里跑着还没回音、已经超过一小时的那些（可能机器没了）。 */
  stuck: number
  /** 离下一次整理还差多少输出 token：`value` 是上次成功后的累计，`threshold` 是阈值。 */
  tokens: { value: number; threshold: number }
}

export interface MemoryProjectRow {
  project_id: string
  name: string
  /** 项目级（共享）记忆条数：非空文件，不含索引、不含被拒的那一版。 */
  entries: number
  /** 私人记忆：只给条数和涉及的人数，没有内容。 */
  personal: { entries: number; owners: number }
  index: MemoryIndexShape
  /** 有文件没进索引。 */
  orphan: number
  /** 索引指着一个不存在的文件。 */
  dangling: number
  /** 正文超过 `BODY_MAX` 的条目数。 */
  over_body: number
  reads: number
  dream: MemoryDreamShape
}

export interface MemoryNumbers {
  projects: { value: number }
  entries: { value: number }
  personal: { value: number; owners: number }
  /** `over` 是撑破预算的项目数（按项目去重）；行数、字节两组分开数。 */
  index: { over: number; over_lines: number; over_bytes: number }
  hygiene: { orphan: number; dangling: number; over_body: number }
  reads: { value: number }
  dream: Omit<MemoryDreamShape, 'tokens'>
}

export interface MemoryReport {
  id: string
  title: string
  summary: string
  days: number
  start: string
  end: string
  numbers: MemoryNumbers
  projects: MemoryProjectRow[]
  trend: { date: string; reads: number }[]
}

export type FeatureDays = 7 | 30 | 90

export function getFeatureCatalogue(): Promise<{ features: FeatureCatalogueEntry[] }> {
  return request<{ features: FeatureCatalogueEntry[] }>('/admin/feature-stats')
}

/** 一条功能的数据。返回类型由调用方点名：每种功能的形状由它自己的模块定，这一层
 *  只知道「按 id 取一份报告」。 */
export function getFeatureReport<T>(featureId: string, days: FeatureDays): Promise<T> {
  return request<T>(`/admin/feature-stats/${encodeURIComponent(featureId)}?days=${days}`)
}

export function getDocsAssistantReport(days: FeatureDays): Promise<DocsAssistantReport> {
  return getFeatureReport<DocsAssistantReport>('docs-assistant', days)
}

export function getTopicNamingReport(days: FeatureDays): Promise<TopicNamingReport> {
  return getFeatureReport<TopicNamingReport>('topic-naming', days)
}

export function getMemoryReport(days: FeatureDays): Promise<MemoryReport> {
  return getFeatureReport<MemoryReport>('memory', days)
}
