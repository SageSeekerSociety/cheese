// 队列页的**地址**那一半：看板深链带进来的问法、栏位切到地址里、日期窗口 chips，以及
// 「清除筛选」那一串动作。
//
// 一份事实一个来源：栏位、搜索词、三个日期窗口既住在 store 里，也写回地址栏，刷新和
// 分享才都作数。拆出来的理由见 `../useAdminQueue.ts` 的文件头。
import type { Ref } from 'vue'
import type { FeedbackStatus } from '@/cx_types'
import type { AdminTab } from '@/stores/feedback'
import type { AdminQueueDeps, AdminQueueStore, QueueWindowKey } from './types'

import { computed, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'

import { allStatuses } from '@/lib/feedbackMeta'
import { relativeDays } from '@/lib/feedbackWindows'

/** 队列的四个**栏位**（服务端的 `ADMIN_TABS`）。写成常量是因为 store 只导出了类型、
 *  没导出这份元组。顺序就是控件上的顺序：最常用的「公开」在最前，「安全」最靠里。
 *
 *  这四个词在这一页上是**服务端的问法**（`GET /admin/feedback?tab=`），不是客户端的筛选
 *  —— 和工具行右边那排状态页签不是一回事：状态页签在**已经拿到的那一页里**筛，栏位换的
 *  是去要哪一批数据。所以两者在控件上必须长得不一样（收在一段底色里的分段控件 vs 裸的
 *  药丸），否则同一行里两排一模一样的东西说着两件不同的事。 */
const ADMIN_TABS: AdminTab[] = ['public', 'private', 'agent', 'security']

/** 这一份要的：`store`、搜索草稿与状态页签、以及页面报回来的搜索焦点回调。 */
export interface AdminQueueRouteDeps {
  store: AdminQueueStore
  statusTab: Ref<FeedbackStatus | 'all'>
  draft: Ref<string>
  pageDeps: AdminQueueDeps
}

/** 地址问法、日期窗口 chips 与「清除筛选」。 */
export function useAdminQueueRoute(deps: AdminQueueRouteDeps) {
  const { store, statusTab, draft, pageDeps } = deps
  const { t } = useI18n()
  const route = useRoute()
  const router = useRouter()

  /* ---- 地址里带过来的问法 ---- */

  /** 看板那几个数字点进来时带的就是这几个（§6.3），旧的 `/admin/feedback?tab=...` 书签
   *  带的也是。返回「有没有已经因此拉过一次」—— 设置器自己会重新取数，这一层就不必再
   *  补一枪，否则一次深链会连打三四个请求。
   *
   *  `status` 不是服务端的参数，它落在状态页签上（在手上这一页里筛）；`assigned` /
   *  `unread` / `hot` 这一层不认识，就不装作认识。 */
  function applyRouteQuery(): boolean {
    const query = route.query
    let loaded = false

    const tab = typeof query.tab === 'string' ? (query.tab as AdminTab) : null
    if (tab && ADMIN_TABS.includes(tab) && store.adminTab !== tab) {
      store.setAdminTab(tab)
      loaded = true
    }

    const status = typeof query.status === 'string' ? query.status : null
    if (status && allStatuses(store.meta?.statuses).includes(status as FeedbackStatus))
      statusTab.value = status as FeedbackStatus

    const windows: [string, string | null, (v: string | null) => void][] = [
      ['since', store.adminSince, (v) => store.setAdminSince(v)],
      ['resolved_since', store.adminResolvedSince, (v) => store.setAdminResolvedSince(v)],
      ['deployed_since', store.adminDeployedSince, (v) => store.setAdminDeployedSince(v)],
    ]
    for (const [key, current, set] of windows) {
      const value = typeof query[key] === 'string' ? (query[key] as string) : null
      if (value && current !== value) {
        set(value)
        loaded = true
      }
    }

    const search = typeof query.q === 'string' ? query.q : null
    if (search && search !== store.adminQuery) {
      draft.value = search
      store.setAdminQuery(search)
      loaded = true
    }

    return loaded
  }

  /** 切栏位。**和地址同步**，理由有两条：
   *
   *   * 刷新之后还停在刚才那一栏。这一页的栏位是**服务端的问法**，换一栏就是换一批数据；
   *     地址里不带它的话，F5 一下人就被悄悄送回「公开」，而他刚才看的可能是私密那一栏。
   *   * 链接发得出去。`/admin/feedback?tab=private` 这条旧书签本来就认（见 `ADMIN_TABS`），
   *     而这一页现在自己也写得出同样的地址 —— 一份事实、一个来源，不会出现「控件上是私密、
   *     地址上是空的」这种两个答案。
   *
   *  用 `replace` 不是 `push`：切栏位不是「去别的地方」，是在同一页里换一个问题，回退键
   *  该回到来处，而不是在四个栏位之间一步步倒。`public` 那一档**不写进地址**（不写就是
   *  它，默认值不占位）。 */
  function selectLane(lane: AdminTab) {
    if (store.adminTab === lane) return
    store.setAdminTab(lane)
    const query = { ...route.query }
    if (lane === 'public') delete query.tab
    else query.tab = lane
    void router.replace({ query })
  }

  /* ---- 日期窗口 chips ---- */

  /** chip 上窗口值的画法：相对窗口（'7d'）画成「近 7 天」，绝对日期原样。store 里存的
   *  就是深链原文（见 `lib/feedbackWindows.ts`），所以这里只看形状、不用管它从哪来。 */
  function windowValueLabel(raw: string): string {
    const days = relativeDays(raw)
    return days === null ? raw : t('feedback.queue.window.days', { n: days })
  }

  /** 每个生效的窗口一颗 chip。**键名逐字写全**（同 `laneOptions` 那条理由：i18n 闸门按
   *  源码字面量认键，拼出来的键它看不见）。 */
  const windowChips = computed(() => {
    const chips: { key: QueueWindowKey; text: string; clearAria: string }[] = []
    if (store.adminSince)
      chips.push({
        key: 'since',
        text: t('feedback.queue.window.since', { v: windowValueLabel(store.adminSince) }),
        clearAria: '',
      })
    if (store.adminResolvedSince)
      chips.push({
        key: 'resolved_since',
        text: t('feedback.queue.window.resolved', { v: windowValueLabel(store.adminResolvedSince) }),
        clearAria: '',
      })
    if (store.adminDeployedSince)
      chips.push({
        key: 'deployed_since',
        text: t('feedback.queue.window.deployed', { v: windowValueLabel(store.adminDeployedSince) }),
        clearAria: '',
      })
    for (const c of chips) c.clearAria = t('feedback.queue.window.clear', { chip: c.text })
    return chips
  })

  /** 把几个键从地址里摘掉。没有那几键时不发 `replace` —— 清筛选不该在历史里留一条
   *  什么都没改的地址。 */
  function dropQueryKeys(keys: string[]) {
    const query = { ...route.query }
    let changed = false
    for (const k of keys) {
      if (k in query) {
        delete query[k]
        changed = true
      }
    }
    if (changed) void router.replace({ query })
  }

  /** 「清除筛选」。关键词和状态页签，加上看板带进来的那三段窗口 —— 按钮写着「清除筛选」，
   *  就该把筛选清干净；只清一半的话，人点完还是一屏空的，会以为按钮坏了。
   *
   *  先改 `draft` 再调 `clearAdminQuery()`：watch 是异步的，等它醒过来时 `store.adminQuery`
   *  已经是空串了，那一条守卫会把它挡掉，于是只有一次请求。 */
  function clearFilters() {
    statusTab.value = 'all'
    draft.value = ''
    store.clearAdminQuery()
    if (store.adminSince) store.setAdminSince(null)
    if (store.adminResolvedSince) store.setAdminResolvedSince(null)
    if (store.adminDeployedSince) store.setAdminDeployedSince(null)
    // 按钮写着「清除筛选」，地址里留着三键的话 F5 会把人送回筛空态。
    dropQueryKeys(['since', 'resolved_since', 'deployed_since'])
  }

  /** 清一个窗口 = 改 store（setter 自己重拉）+ 把地址里的那一键摘掉 —— 和 `selectLane`
   *  写回 tab 是同一条规矩：一份事实一个来源，F5 不该复活人刚亲手清掉的筛选。 */
  function clearWindow(key: QueueWindowKey) {
    if (key === 'since') store.setAdminSince(null)
    else if (key === 'resolved_since') store.setAdminResolvedSince(null)
    else store.setAdminDeployedSince(null)
    dropQueryKeys([key])
    // chip 没了焦点不能落空：送到旁边的搜索框（鼠标点的不出焦点环，:focus-visible 管这件事）。
    void nextTick(() => pageDeps.focusSearch())
  }

  return { applyRouteQuery, selectLane, windowChips, clearFilters, clearWindow }
}
