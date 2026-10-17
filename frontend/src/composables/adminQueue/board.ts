// 队列页「手上这一页」的那一半：可见行、当前行、状态页签那几排选项，以及四态与文案。
//
// 只读 store 和页面自己的三个状态（视图、状态页签、光标），不碰 DOM、不发请求、不写
// store —— 拆出来的理由见 `../useAdminQueue.ts` 的文件头。队列页读的 `visible` /
// `current` / `state` / `copy` 全在这里算好，组合根原样转给页面。
import type { Ref } from 'vue'
import type { FeedbackCard, FeedbackStatus } from '@/cx_types'
import type { AdminTab } from '@/stores/feedback'
import type { AdminQueueStore, QueueView } from './types'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import { allStatuses, statusMeta } from '@/lib/feedbackMeta'

/** 这一份要的那几个页面状态：`store` 由组合根持有，其余三个是这里推导的输入。 */
export interface AdminQueueBoardDeps {
  store: AdminQueueStore
  view: Ref<QueueView>
  statusTab: Ref<FeedbackStatus | 'all'>
  cursorId: Ref<string | null>
}

/** 「手上这一页」和四态。返回的都是推导值，没有一个能写。 */
export function useAdminQueueBoard(deps: AdminQueueBoardDeps) {
  const { store, view, statusTab, cursorId } = deps
  const { t } = useI18n()

  /* ---- 手上这一页 ---- */

  const tabs = computed<(FeedbackStatus | 'all')[]>(() => ['all', ...allStatuses(store.meta?.statuses)])
  const items = computed(() => store.adminItems)

  /** 状态页签那一排（`AdminTabs`）要的形状：`{ value, label }`。文案口径和原来那颗裸
   *  药丸逐字一致 —— `all` 走 `feedback.queue.tab.all`，其余走 `statusMeta().label`
   *  （那一份是呈现词表，不从 i18n 取，见 `lib/feedbackMeta.ts` 文件头）。 */
  const tabOptions = computed(() =>
    tabs.value.map((tab) => ({
      value: tab,
      label: tab === 'all' ? t('feedback.queue.tab.all') : statusMeta(tab).label,
    }))
  )

  /** 四个栏位各自的名字。**写成一张字面量表，不拼键** —— i18n 的闸门
   *  （`src/i18n/catalog.spec.ts`）是照源码里的字面量认「这个键有人用」的，拼出来的键
   *  既不算调用、真叶子又会被判成没人引用。 */
  const laneOptions = computed<{ value: AdminTab; label: string }[]>(() => [
    { value: 'public', label: t('feedback.queue.lane.public') },
    { value: 'private', label: t('feedback.queue.lane.private') },
    { value: 'agent', label: t('feedback.queue.lane.agent') },
    { value: 'security', label: t('feedback.queue.lane.security') },
  ])

  /** 状态页签**在手上这一页里筛**（见文件末尾的代价）。`all` 那一档不复制数组：返回的
   *  就是 store 那一份，`v-for` 的 key 也因此不重算。 */
  const visible = computed(() =>
    statusTab.value === 'all' ? items.value : items.value.filter((item) => item.status === statusTab.value)
  )

  /** 混合列表里要写状态字；单状态的页签下它是一列重复四遍的字，省掉（§5.2）。 */
  const showStatusWord = computed(() => statusTab.value === 'all')

  const cursorIndex = computed(() => {
    const at = visible.value.findIndex((item) => item.id === cursorId.value)
    // 找不到（还没选、或那一条被筛走了）时回到第一行：没有当前行的队列 Tab 不进来。
    return at >= 0 ? at : visible.value.length ? 0 : -1
  })

  const current = computed<FeedbackCard | null>(() =>
    cursorIndex.value >= 0 ? visible.value[cursorIndex.value] : null
  )

  const unread = computed(() => store.counts.unread ?? 0)

  /** 首次加载才给骨架。已经有内容时换骨架会让整页闪一下，那是比「旧内容多停半秒」更糟
   *  的手感 —— 所以 `store.adminLoading` 只在手上一条都没有时才算数。 */
  const showSkeleton = computed(() => store.adminLoading && !items.value.length)

  /** 队列自己的读失败。**列表空着才算**：手上还有一页旧数据时，一次失败的刷新不该把
   *  那一页换成一句错误 —— 它只是不新鲜了。 */
  const listError = computed(() => (!items.value.length && !store.adminLoading ? store.error : null))

  /** 详情拉失败的原话。**只在「我要的那条」回来之后才算数**：`store.error` 是共用的，
   *  一次失败的列表刷新不该在详情里画出一句读失败。 */
  const detailError = computed(() =>
    store.detailId && store.detailId === current.value?.id && !store.detailLoading && !store.detail ? store.error : null
  )

  /** 有筛选条件吗。三个日期窗口也算 —— 看板的数字点进来时带的就是它们，那时结果空了要说
   *  「没有符合条件的反馈」而不是「还没有人提交反馈」。 */
  const hasFilter = computed(
    () =>
      statusTab.value !== 'all' ||
      !!store.adminQuery.trim() ||
      !!(store.adminSince || store.adminResolvedSince || store.adminDeployedSince)
  )

  /** 四态里的第三态。空 = 一条都没有且没有任何筛选；筛空 = 有筛选但零命中。**两者必须分开**：
   *  「平台还没有反馈」和「你要找的那条被筛掉了」是两件事，前者会让人以为平台坏了。 */
  const state = computed<'error' | 'filtered' | 'empty' | null>(() => {
    if (visible.value.length) return null
    // `!== null`：`null` 才算没失败，空串是「失败了但服务端没给话」。用真值判会把这种
    // 失败落进「暂无反馈」，把接口挂掉画成平台是空的。
    if (listError.value !== null) return 'error'
    if (items.value.length) return 'filtered'
    return hasFilter.value ? 'filtered' : 'empty'
  })

  /** 四态文案。**键名逐字写全**，不做 `` t(`${ns}.error.title`) `` 那种拼接：i18n 闸门
   *  是按源码里的字面量扫引用的，拼出来的键在它眼里等于没人用（`catalog.spec.ts`）。
   *
   *  出错那两态的说明行放**服务端原话**，不放「检查网络后重试。」这种固定话：失败未必是
   *  网络（没权限、限流、后端 500 都长这样），吞掉原因就等于把人往错的方向支。服务端没
   *  给话时不画那一行（空说明行只占地方），标题和重试照旧 —— 失败本身不能因此不报。 */
  const copy = computed(() => {
    if (state.value === 'error') {
      return view.value === 'list'
        ? {
            title: t('feedback.queue.error.title'),
            desc: listError.value || undefined,
            action: t('feedback.queue.error.retry'),
          }
        : {
            title: t('feedback.table.error.title'),
            desc: listError.value || undefined,
            action: t('feedback.table.error.retry'),
          }
    }
    if (state.value === 'filtered') {
      return view.value === 'list'
        ? {
            title: t('feedback.queue.filtered.title'),
            desc: t('feedback.queue.filtered.desc'),
            action: t('feedback.queue.filtered.clear'),
          }
        : {
            title: t('feedback.table.filtered.title'),
            desc: t('feedback.table.filtered.desc'),
            action: t('feedback.table.filtered.clear'),
          }
    }
    return view.value === 'list'
      ? { title: t('feedback.queue.empty.title'), desc: t('feedback.queue.empty.desc'), action: '' }
      : { title: t('feedback.table.empty.title'), desc: t('feedback.table.empty.desc'), action: '' }
  })

  return {
    tabOptions,
    laneOptions,
    items,
    visible,
    showStatusWord,
    cursorIndex,
    current,
    unread,
    showSkeleton,
    listError,
    detailError,
    state,
    copy,
  }
}
