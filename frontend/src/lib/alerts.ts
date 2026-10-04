// 变更提醒的批量收起：一次标掉调用者自己的整队，而不是一条一条点。
//
// 放在 `lib/` 而不是 `api.ts`：后者已经在上限之上（`.claude/scripts/check-file-sizes.py`），
// 只能变短；组件的边界规则（`scripts/import-boundary-ratchet-core.mjs`）也不许组件再添
// 一条 API 层的 import。调用方是一个组件（`components/NeedsYou.vue`），和
// `lib/attachments.ts`、`lib/adminStats.ts` 同一个去处。
import { request } from '../api'

/** 把这个项目里、调用者名下还没读的通知一次标掉，返回标掉了多少条。
 *
 *  后端只标调用者自己的，而且没拍板的决策请求故意留着（`mark_all_read_in_project`），
 *  所以这一下清掉的是一整队变更提醒和读过就走的验收卡，不是还没答的问题。 */
export function markAllAlertsRead(projectId: string, targetHandle: string): Promise<{ marked: number }> {
  return request<{ marked: number }>(
    `/projects/${encodeURIComponent(projectId)}/alerts/read-all?target_handle=${encodeURIComponent(targetHandle)}`,
    { method: 'POST' }
  )
}
