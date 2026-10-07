// 后台统计页**取数**的那一半：窗口、轮询、时效戳、重试。一页一类（`kind`），画法在
// `AdminStatsPageView` 和 `components/admin/dashboard/*.vue`。
//
// 这几页原来是同一页看板上的七屏，按「来后台要干什么」拆进了侧栏的各组（见
// `@/lib/adminSections`）。数据仍在 store 里按类缓存：从一页切到另一页再切回来，看到的是
// 刚才那一份，「重新拉一次」是刷新键或轮询的事。
import type { PendingRow, StatsDays, StatsKind } from '@/lib/adminStats'

import { computed, onBeforeUnmount, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'

import { useFeedbackStore, WINDOWED_KINDS } from '@/stores/feedback'

/** 迷你列表最多画几行。和 `AdminNumberList` 的 `SHOWN` 是同一个数。 */
const SHOWN = 10

/** 轮询只覆盖「这一刻」的两类（平台健康、接口耗时），60s。窗口类（交付/产品/反馈/
 *  用量）有手动 R 和切窗口已经足够；integrations 是存量慢变。 */
const POLL_KINDS: StatsKind[] = ['platform', 'performance']
const POLL_MS = 60_000

/** 「HH:MM」（补零）。直接读 `Date` 的时分，不把字符串交给 `Date` 解析。 */
function hhmm(at: number): string {
  const d = new Date(at)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

/** 「FB-1042」里那串数字。列表左列要的是一个能纵向对齐、能排序的编号。 */
function displayNo(displayId: string): number {
  const digits = displayId.replace(/\D/g, '')
  return digits === '' ? 0 : Number(digits)
}

export function useAdminStats(kind: StatsKind) {
  const store = useFeedbackStore()
  const { t } = useI18n()

  // store 里「当前那一类」跟着这一页走：不带参数的 `loadStats()`（R 键）读它。
  store.statsKind = kind

  let pollTimer: number | undefined

  const days = computed(() => store.statsDays)
  /** 这一类的数有「过去 N 天」这个说法吗（性能读进程内存、集成是存量，都没有）。 */
  const windowed = WINDOWED_KINDS.includes(kind)
  const loading = computed(() => store.statsBusy[kind])
  const error = computed(() => store.error)
  /** 整页失败 = 这一类什么都没拿到，而 store 里有一句服务端的原话。 */
  const failed = computed(() => !store.statsBusy[kind] && store.stats[kind] === null && store.error !== null)

  const feedback = computed(() => store.stats.feedback)
  const usage = computed(() => store.stats.usage)
  const platform = computed(() => store.stats.platform)
  const pipeline = computed(() => store.stats.pipeline)
  const product = computed(() => store.stats.product)
  const performance = computed(() => store.stats.performance)
  const integrations = computed(() => store.stats.integrations)

  /** 页头那句「更新于 HH:MM」。还没成功拉到过（`statsAt` 为 null）时是空串；超过 5 分钟
   *  追加「· N 分钟前」—— 切回这一页时一眼看出这份数据有多旧。 */
  const stampText = computed(() => {
    const at = store.statsAt[kind]
    if (at === null) return ''
    const ageMin = Math.floor((Date.now() - at) / 60_000)
    return ageMin >= 5
      ? t('feedback.dashboard.updatedAtStale', { time: hhmm(at), n: ageMin })
      : t('feedback.dashboard.updatedAt', { time: hhmm(at) })
  })

  /** 反馈趋势页右侧迷你列表的行。「需处理」只有两种状态（还没人接手、有人在做）；更新
   *  时间取 `last_activity_at`，为空退回 `created_at`。 */
  const pending = computed<PendingRow[]>(() =>
    store.adminItems
      .filter((row) => row.status === 'received' || row.status === 'in_progress')
      .slice(0, SHOWN)
      .map((row) => ({
        id: row.id,
        no: displayNo(row.display_id),
        title: row.title,
        status: row.status,
        updatedAt: row.last_activity_at ?? row.created_at,
      }))
  )

  /** 迷你列表的加载态（它走的是 `loadAdmin`，不在 `loadStats` 里）。 */
  const listLoading = computed(() => store.adminLoading)

  /** 换窗口。store 里那一条会把已加载的窗口类全部重拉（缓存键=窗口）。 */
  function setDays(next: StatsDays) {
    store.setStatsDays(next)
  }

  function pollTick() {
    if (document.visibilityState !== 'visible') return
    void store.loadStats(kind, store.statsDays)
  }

  /** 回到可见时补一次（如果已经旧过一个周期）。轮询失败不打扰 —— `loadStats` 失败只写
   *  `store.error`，旧数据配旧时间戳还在，错误块只在「什么都没拿到」时整屏。 */
  function onVisibleAgain() {
    if (document.visibilityState !== 'visible') return
    const at = store.statsAt[kind]
    if (at !== null && Date.now() - at > POLL_MS) void store.loadStats(kind, store.statsDays)
  }

  /** 错误块的重试：**真重拉**这一类；反馈类顺带把迷你列表那一路也重拉。 */
  function retry() {
    void store.loadStats(kind, store.statsDays)
    if (kind === 'feedback') void store.loadAdmin()
  }

  onMounted(() => {
    // 只补拉「这一类还没有数据」的情形。`loadStats` 只写 `statsBusy`、不在完成前写
    // `stats`，所以首次挂载时这句必然成立一次；别在这之外再补第二句，否则同一类两个并发
    // 请求，store 的序号守卫会把先回来的那个响应丢掉。
    if (store.stats[kind] === null) void store.loadStats(kind, store.statsDays)
    // 反馈趋势页的迷你列表走队列那一路，和统计并发拉。
    if (kind === 'feedback') void store.loadAdmin()

    if (POLL_KINDS.includes(kind)) {
      pollTimer = window.setInterval(pollTick, POLL_MS)
      document.addEventListener('visibilitychange', onVisibleAgain)
    }
  })

  onBeforeUnmount(() => {
    window.clearInterval(pollTimer)
    document.removeEventListener('visibilitychange', onVisibleAgain)
  })

  return {
    days,
    windowed,
    loading,
    error,
    failed,
    stampText,
    pending,
    listLoading,
    pipeline,
    product,
    feedback,
    usage,
    platform,
    performance,
    integrations,
    setDays,
    retry,
  }
}
