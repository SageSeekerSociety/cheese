// 模型管理那一页拆开之后，几块共用的一点点东西：服务端原话怎么取、窗口有哪几档、
// 审计一次拉几条，以及写的那几块之间递的那条横幅。
//
// 为什么单独一份：`message` 四段都在用（四条失败各说各的原话），`WINDOWS` 是页头的
// 档位而三段共用同一个窗口（窗口是**页面**的问题，不是某一段的问题）。放进任何一段
// 都会让别段反过来依赖它。
import type { Ref } from 'vue'

/** 审计那一段最多拉几条。后端默认 50，和这里的 50 是同一个数。 */
export const AUDIT_LIMIT = 50

/** 页头的三个窗口档位。三段共用。 */
export const WINDOWS = [7, 14, 30]

/** 服务端的原话在 `message` 里（`ApiError` 带上来的）。取不到才用兜底那句。 */
export function message(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback
}

/** 页顶那条横幅：写失败与提示互斥，同一时刻只画一条。写的那几块往里放，页头读它。
 *  两个 ref 由门面持有（`useAdminModels.ts`），因为放东西进去的人不止一个。 */
export interface AdminModelsBanner {
  /** 写操作（删除 / 停用 / 改额度）失败的原话。 */
  writeError: Ref<string | null>
  /** 写成功之后那句提示。 */
  notice: Ref<string | null>
}

/** 写完要重拉模型那一段的三块（模型、线路）共用的那只手：读在 `adminModels/reads.ts`。 */
export interface AdminModelsReload {
  load: () => Promise<void>
}
