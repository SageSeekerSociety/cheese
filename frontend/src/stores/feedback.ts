/**
 * stores/feedback.ts — 反馈功能的**全部状态**，接真接口。
 *
 * 它是这一层存在的唯一理由：页面和组件一律通过 action / getter 读写，谁都不许自己
 * 发请求。上一轮它是纯内存的原型（动作同步改本地数组），这一轮函数体换成了网络调用，
 * 而**调用方几乎没有改** —— 那正是当初把它单独拆出来的目的。
 *
 * 三件不再由前端决定的事：
 *
 *   1. **权限**。「我是不是管理员」读服务端的 `GET /feedback/meta`（`meta.is_admin`），
 *      不再是一个可以拨的开关。上一轮那个「原型身份」开关整块删掉了 —— 它当时就写着
 *      「不是权限门」，现在门在服务端，客户端连猜都不该猜。
 *   2. **隐私**。私密条目由服务端 `WHERE` 决定谁能看见（提交者 ∪ 管理员 ∪ 提出它的房间），前端不做
 *      第二遍过滤：客户端过滤只能挡住界面，挡不住链接。上一轮那句「将来这层整个删掉」
 *      说的就是现在。
 *   3. **有哪些状态、按什么顺序走**。词表来自 `GET /feedback/meta`；颜色留在
 *      `lib/feedbackMeta.ts`（那是视觉决定，服务端不该知道 `--warn-wash`）。
 *
 * 列表的 Tab 和搜索都是**服务端**参数，所以切 Tab / 改搜索词会重新拉一页，而不是在
 * 本地数组上 filter。搜索键击用 250ms 防抖，而且只有最新一次请求的结果会被采纳
 * （见 `feedback/session.ts` 里那几把请求序号），否则慢的那次后到，会把快的那次覆盖掉。
 */

import type { FeedbackStatus } from '@/cx_types'
import type { FeedbackTab } from './feedback/types'

import { defineStore } from 'pinia'

import { adminActions } from './feedback/actions/admin'
import { composeActions } from './feedback/actions/compose'
import { detailActions } from './feedback/actions/detail'
import { internalsActions } from './feedback/actions/internals'
import { listActions } from './feedback/actions/list'
import { statsActions } from './feedback/actions/stats'
import { PAGE_SIZE } from './feedback/helpers'
import { initialState } from './feedback/state'

import { STATUS_LADDER } from '@/lib/feedbackMeta'

/**
 * 反馈这一层还是**一条 store**，名字、state、getters、动作一个都没变 —— 调用方零
 * 改动，`storeToRefs`、`$patch`、模板里那些 `store.xxx` 也照旧。
 *
 * 变的是**这个文件的大小**：1708 行里 store 定义本身就占 1334 行，所以光把辅助函数
 * 搬出去到不了 1000 行的上限；动作按职责搬进了 `feedback/actions/` 下的几个模块，
 * 每个模块自己声明「我跑起来时手上是什么」（`this: …Self`），拼回这里还是原来那条。
 *
 * 原来放在这里的类型、常量、纯函数和那份**不在 state 里**的状态，分别搬到了
 * `feedback/types.ts`、`feedback/helpers.ts` 和 `feedback/session.ts`；它们照旧从
 * 这里原样转出去。
 *
 * **getters 留在本文件**：它们的 `state` 参数没有标注，靠的正是这个位置的上下文推断
 * （`defineStore` 从 `state` 推出 S）—— 搬出去立刻变成隐式 any。
 */
export const useFeedbackStore = defineStore('feedback', {
  state: initialState,
  getters: {
    /** 我是不是反馈管理员（不是平台管理员，见 meta.is_platform_admin）。服务端说了算。 */
    isAdmin(state): boolean {
      return !!state.meta?.is_admin
    },
    /** 状态梯子：服务端给的那份，没到之前用本地兜底（见 lib/feedbackMeta.ts）。 */
    statusLadder(state): FeedbackStatus[] {
      const fromServer = state.meta?.status_ladder
      return fromServer?.length ? fromServer : STATUS_LADDER
    },
    /** 「热门」的三个数：够多少分（`hotThreshold`）、一个支持几天打对折
     *  （`hotHalfLifeDays`）、不够分时至少补几条（`hotMinItems`）。
     *
     *  **前端不拿它们排序**：筛选和排序都在服务端，客户端手上的已经是排好的行，
     *  再算一遍屏幕上就有两套热度。它们在这里只有一个用处 —— 把这一栏的规则**说给
     *  人听**（反馈中心那一行小字）。一个「支持数」说不清「上周爆的和今天爆的同权」，
     *  半衰期这个数才说得清。
     *
     *  兜底值和服务端的常量一致（见 `repositories.py` 的 `HOT_*`）：meta 拿不到时
     *  那一行照样说得通，不会显示一个空白。 */
    hotThreshold(state): number {
      return state.meta?.hot_score ?? 2
    },
    hotHalfLifeDays(state): number {
      return state.meta?.hot_half_life_days ?? 14
    },
    hotMinItems(state): number {
      return state.meta?.hot_min_items ?? 5
    },
    /** 后面还有没有。判据是**取回来的行数**和 `total`，不是 `items.length`：后者是
     *  去重之后的长度，有人插队时会永远小于 `total`，按钮就永远在（见
     *  `listRequested`）。仍然**用服务端给的 `total` 判**，不用「这一页是不是满的」：
     *  一页 50 条、总共正好 50 条时，「满员」会让人多按一次才看见空页。 */
    listHasMore(state): boolean {
      return state.listRequested < state.total
    },
    mineHasMore(state): boolean {
      return state.mineRequested < state.mineTotal
    },
    /** 当前停着的那一类在不在加载中。页面读的是它，所以「切到另一类」不会把已经拿到的
     *  这一类按成骨架。 */
    statsLoading(state): boolean {
      return state.statsBusy[state.statsKind]
    },
    tabCounts(state): Record<FeedbackTab, number> {
      return {
        all: state.counts.all,
        hot: state.counts.hot,
        resolved: state.counts.resolved,
      }
    },
    /** 管理端的翻页。**边界由自己手上的那一页算，不靠服务端**：`adminTotal` 是
     *  整个栏位的条数，用它算「下一页还有没有」需要知道每页多大、而且翻到最后一
     *  页时会多按一次才发现是空的。手上这一页满员就还有下一页 —— 只有一条边界
     *  情况（最后一页正好满员），代价是那一次点下去会看到空页，而反过来（先按了
     *  才去问服务端）要多一次往返。 */
    adminHasPrev(state): boolean {
      return state.adminPageStart > 0
    },
    adminHasNext(state): boolean {
      return state.adminItems.length >= PAGE_SIZE
    },
  },
  actions: {
    ...listActions,
    ...detailActions,
    ...composeActions,
    ...adminActions,
    ...statsActions,
    ...internalsActions,
  },
})

export { cleanTags, EXPECTATION_KINDS, MAX_TAGS, REPRO_KINDS, WINDOWED_KINDS } from './feedback/helpers'
export { resetFeedbackCaches } from './feedback/session'
export type { AdminSort, AdminTab, FeedbackDraft, FeedbackTab } from './feedback/types'
