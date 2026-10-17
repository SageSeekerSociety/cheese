// `useAdminQueue` 拆开之后几块共用的类型：组合根（`../useAdminQueue.ts`）和几个子模块
// 都读它，所以它单独放一处，而不是挂在组合根上。
import type { useFeedbackStore } from '@/stores/feedback'

/** 反馈队列 store 的类型。组合根持有这一份实例，往下发。 */
export type AdminQueueStore = ReturnType<typeof useFeedbackStore>

/** 分段控件的两个档。**档位的名字由页面这一层定**：`view` 是它的状态，而两档画成什么
 *  样子是 `AdminQueueHeader` 的事（它自己那张 `VIEWS` 表，值就是这个类型）。 */
export type QueueView = 'list' | 'table'

/** 地址里那三个窗口键。和 store 的三个字段一一对应，但存的是**键名**：chip 上画的、
 *  地址里摘的都是它。导出是因为 `AdminQueueToolbar` 报回来的那一颗 chip 也要说清是
 *  哪一个（三个 setter 各是一件事，`key` 是它们的名字）。 */
export type QueueWindowKey = 'since' | 'resolved_since' | 'deployed_since'

/** 搜索框住在 `AdminQueueToolbar` 里，而 `/` 键和 chip 清空都把焦点送进去。搜索框的
 *  位置是**页面**的事（那一件只吃 props），所以页面把那件事包成一个回调交给这一层。
 *  同理 `focusAssignee`：详情里那颗 combobox 住在 View 里，宽屏那一档在页面正文、
 *  窄屏那一档在抽屉里，两个实例长什么样只有 View 知道。 */
export interface AdminQueueDeps {
  focusSearch: () => void
  focusAssignee: () => void
}
