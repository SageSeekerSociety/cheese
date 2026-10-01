// 棘轮页（`/admin/ratchet`）的 HTTP 层与形状。
//
// 为什么单独一个文件：`src/api.ts` 早就过了 1000 行的上限（`.claude/rules/architecture.md`
// 只许变小），这一片跟着服务端自己的目录走，和 `views/admin/features/featureApi.ts` 同一个理由。
//
// 这一层**一个数都不算**。所有判断（哪个方向算好、哪一段能比、哪一格是洞）都在服务端
// 的 `app/domain/ratchet/board.py` 里做完了，前端只把 `direction` 翻成人话。在这里
// 再算一遍等于给同一件事写第二份规则，而第二份总会晚一步。
import { request } from '@/api'

/** 一次采集里的一个点。`actual` 是 `null` 就是**没量到**（采集失败，或这次没跑这道检查），
 *  不是 0 —— 0 的意思是「量过了、没有问题」，这两件事混起来是这一页最不能犯的错。 */
export interface RatchetPoint {
  commit: string
  collected_at: string | null
  run_url: string
  collection: string
  status: string
  actual: number | null
  frozen: number | null
  stale_count: number | null
  rule_fingerprint: string | null
  rule_changed: boolean
  new_exemptions: number | null
  details: unknown[] | null
  reason: string | null
}

/** 一条已登记但树上已经找不到对应物的豁免。它是一句「这条债还了，登记没清」，
 *  不是还欠着。 */
export interface RatchetStaleExemption {
  file?: string
  why?: string
  frozen?: number
  actual?: number
}

/** 整个仓库（树）的超限情况，只有 `file-sizes` 这一道带 —— 它是一个 **diff 口径**的
 *  闸门，`actual` 数的是「这次改过、并且超了上限的文件」。采集跑在 main 的合并提交上
 *  时工作树没有差异，那个数就是 0，读起来却像「树上没有超限文件」。树级数字从采集器
 *  自己的板子里来，和检查记录存在同一份快照里。
 *
 *  `null` = 这次采集没有量到树（板子没测出来），**不是 0** —— 页面写「未知」。 */
export interface RatchetTreeSize {
  offenders: number
  excess_lines: number
  caps: {
    prefix: string
    cap: number | null
    judged: number | null
    over_cap: number | null
    excess_lines: number | null
  }[]
}

export interface RatchetCheck {
  id: string
  area: string
  /** 哪个方向算好。`down` = 数越小越好（债的数量），`up` = 越大越好。 */
  better: 'down' | 'up' | null
  /** 服务端按「最后一段同口径」算出来的走向，前端只翻译不重算。 */
  direction: 'improving' | 'worse' | 'flat' | 'unknown'
  status: string
  actual: number | null
  frozen: number | null
  stale: RatchetStaleExemption[]
  stale_count: number | null
  rule_fingerprint: string | null
  points: RatchetPoint[]
  /** 只有 `file-sizes` 有；`undefined` = 这一道检查没有树级数字这回事。 */
  tree?: RatchetTreeSize | null
}

export interface RatchetArea {
  area: string
  checks: RatchetCheck[]
}

/** 一次采集在归档里的样子（时间线用）。 */
export interface RatchetCollection {
  commit: string
  collected_at: string | null
  run_url: string
  collection: string
  reason: string | null
}

export interface RatchetBoard {
  repo: string
  generated_at: string
  /** 正在跑的部署版本。和 `collected_commit` 不是一个东西：一个是线上现在跑什么，
   *  一个是这份数据量的是哪个提交。 */
  deployed_commit: string
  collected_commit: string | null
  collected_at: string | null
  run_url: string | null
  collection: string | null
  points: number
  total_stored: number
  collections: RatchetCollection[]
  areas: RatchetArea[]
  /** 只有 `POST /refresh` 的响应带这一块。 */
  refresh?: RatchetRefreshReport
}

/** 一次拉取的结果，原样来自服务端：`listed` 是 CI 上看到几份工件，`stored` 是新入库几份。
 *  两者的差是「已经存过」——把这两个数分开，是因为「CI 上有 50 份」和「这次新存了 1 份」
 *  读起来像同一件事，做起来完全是两回事。 */
export interface RatchetRefreshReport {
  repo: string
  listed: number
  stored: number
  already_stored: number
  unreadable: number
  failed: number
  error: string
}

/** 归档里现在的走势。**不碰 GitHub**：刷新的那一秒外部挂了，这一页照样读得出东西。 */
export function getRatchetBoard(): Promise<RatchetBoard> {
  return request<RatchetBoard>('/admin/ratchet')
}

/** 去 CI 拉一次新采集。失败时服务端照样返回 200，把原因放在 `refresh.error` 里 ——
 *  拉不到不是这一页挂了，页面上那些已经存下的点还是真的。 */
export function refreshRatchetBoard(): Promise<RatchetBoard> {
  return request<RatchetBoard>('/admin/ratchet/refresh', { method: 'POST' })
}
