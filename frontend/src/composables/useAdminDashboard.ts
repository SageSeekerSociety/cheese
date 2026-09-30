// 看板页**取数**的那一半：分类的缓存、窗口、轮询、时效戳、重试。
//
// 分家的理由和 `usePanelChanges` 是同一个形状：这一页原先一份 `<script setup>` 一千
// 三百行，取数和画法长在一起，于是每一屏都拿不出来单独看 —— 挂在预览站里得先立一个
// 假后端。现在三件事各归各位：
//
//   - 取数（store 的哪一格、窗口、轮询、时间戳、重试）→ 这里；
//   - 画（每屏的数、拆分、曲线、表）→ `components/admin/dashboard/*.vue`，只吃 props、
//     只往上发事件；
//   - 接线（图上点一天跳队列）→ 页面自己。
//
// 页头那条分类导轨的两份数据也在这里算：短值（`pulse`）和页签的提示句（`titles`）都是
// 「几类一起看」的东西，落在任何一屏里都不合适。
import type { PendingRow, PulseRow, StatsDays, StatsKind } from '@/lib/adminStats'

import { computed, onBeforeUnmount, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'

import { fmtNum, fmtSI } from '@/lib/usageFormat'
import { useFeedbackStore, WINDOWED_KINDS } from '@/stores/feedback'

/** 分类的顺序就是这里的顺序。三类**一一对应服务端那三条接口**，不多不少：把「账号」
 *  和「机器」拆成两个分类的话，它们会各拉一次同一条 `/admin/stats/platform`。 */
const KINDS: StatsKind[] = ['pipeline', 'product', 'feedback', 'usage', 'platform', 'performance', 'integrations']

/** 每个分类的名字。**写成一张键名字面量的表**，不在模板里拼
 *  `feedback.dashboard.tab.${kind}` —— 拼出来的键在源码里没有一处字面量出现，
 *  `catalog.spec.ts` 的「这个键没有任何文件引用」那条闸门就会把这三个键判成没人用的
 *  死词条（它扫的是源码文本，不是运行时的调用）。拼字符串在这里省下的是一行，代价是
 *  每次跑门禁都要重新解释一遍「这三个键其实是活的」。 */
const TAB_KEY: Record<string, string> = {
  pipeline: 'feedback.dashboard.tab.pipeline',
  product: 'feedback.dashboard.tab.product',
  feedback: 'feedback.dashboard.tab.feedback',
  usage: 'feedback.dashboard.tab.usage',
  platform: 'feedback.dashboard.tab.platform',
  performance: 'feedback.dashboard.tab.performance',
  integrations: 'feedback.dashboard.tab.integrations',
}

/** 迷你列表最多画几行。和 `AdminNumberList` 的 `SHOWN` 是同一个数。 */
const SHOWN = 10

/** 轮询只覆盖「这一刻」的两类（平台健康、接口耗时），60s。窗口类（交付/产品/反馈/
 *  用量）有手动 R 和切窗口已经足够；integrations 是存量慢变（它的死信/未发在
 *  performance.reliability 里有同源读数，沾性能类的轮询光）。 */
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

/** 20 万 token 这种短写 —— 导轨上摆 `204,900` 是把下面 KPI 的同一个数再念一遍。
 *  走 `fmtSI` 而不是手写阶梯：手写的那份止步于 M，`1e12` 会被打成 `1000000.0M`。 */
const shortTokens = (n: number | null | undefined): string => (n === null || n === undefined ? '—' : fmtSI(n))

export function useAdminDashboard() {
  const store = useFeedbackStore()
  const { t } = useI18n()

  let pollTimer: number | undefined

  const kind = computed(() => store.statsKind)
  const days = computed(() => store.statsDays)
  /** 这一类的数有「过去 N 天」这个说法吗（性能读进程内存、集成是存量，都没有）。 */
  const windowed = computed(() => WINDOWED_KINDS.includes(store.statsKind))
  const loading = computed(() => store.statsLoading)
  const error = computed(() => store.error)
  /** 整页失败 = 当前那一类什么都没拿到，而 store 里有一句服务端的原话。 */
  const failed = computed(() => !store.statsLoading && store.stats[kind.value] === null && store.error !== null)

  const feedback = computed(() => store.stats.feedback)
  const usage = computed(() => store.stats.usage)
  const platform = computed(() => store.stats.platform)
  const pipeline = computed(() => store.stats.pipeline)
  const product = computed(() => store.stats.product)
  const performance = computed(() => store.stats.performance)
  const integrations = computed(() => store.stats.integrations)

  /** 压着没人管的急件。导轨上反馈那一格的有事信号（反馈那一屏自己也读这个数：那边
   *  的判据是「画不画那一行警示」，和这里是两个用途）。 */
  const urgentOpen = computed(() => feedback.value?.total.urgent_open ?? 0)

  /** 最慢那条路由的 p95 —— 「哪条慢」是性能那一块唯一要回答的问题。取**最大值**
   *  而不是第一个元素：fixture 与旧响应都给出过未排序的数组。 */
  const slowestP95 = computed(() => {
    const rows = performance.value?.routes ?? []
    const best = rows.reduce<number | null>((acc, row) => {
      if (row.p95 === null || row.p95 === undefined) return acc
      return acc === null || row.p95 > acc ? row.p95 : acc
    }, null)
    return best === null ? '—' : fmtSI(best)
  })

  /** 顶上那条分类导轨 —— 这一页的**第一眼**。
   *
   *  分类控件把七块藏在七个抽屉里，于是「反馈在催、用量在涨」这件事要么点三下才看见，
   *  要么根本看不见。这一条把每一块的那**一个**数摆在一起：读的人先知道「现在哪块
   *  需要我」，再决定进哪一块。
   *
   *  每块只取一个数（不是四个）：一行摆四个指标 × 七块 = 二十几个数，那就不是摘要而是
   *  另一张表。取哪一个，判据是「这一块现在最该被看见的那件事」：
   *
   *   * 交付 → **等你**（有事等着人动）；用量 → 窗口内的 **token**（量级，比钱稳）；
   *   * 产品 → **验收通过**（这一块存在的理由）；反馈 → **待分诊**（有急件时换成急件
   *     数并染警示色）；平台 → **健康度**；性能 → **最慢那条的 p95**；集成 → **死信**。
   *
   *  点一块就切到那一类 —— 它是导航，不是卡片（`to` 语义上的链接由下面的 KPI 卡承担）。
   *
   *  **「没读到」和「是零」必须分开**：那一类还是 null 时短值画「—」，不是「等你 0」
   *  （`?? 0` 曾经让没加载的类显示一个假 0 —— 全站纪律：没读到画破折号，不画 0）。 */
  const pulse = computed<PulseRow[]>(() => {
    const row = (key: StatsKind, value: string, hint: string, tone: PulseRow['tone']): PulseRow =>
      store.stats[key] === null ? { key, value: '—', hint, tone: 'ink' } : { key, value, hint, tone }
    return [
      row(
        'pipeline',
        // 「等你」是这一块唯一要回答的问题，而且它**不在**下面的 KPI 第一张
        // （第一张是「还在走」）。
        t('feedback.dashboard.pipeline.kpi.needsYouShort', {
          n:
            (pipeline.value?.needs_you.reviewer_pending ?? 0) +
            (pipeline.value?.needs_you.open_tasks ?? 0) +
            (pipeline.value?.needs_you.awaiting_answer ?? 0),
        }),
        t('feedback.dashboard.pipeline.needs.title'),
        'ink'
      ),
      row(
        'product',
        fmtNum(product.value?.north_star.total ?? 0),
        t('feedback.dashboard.product.northStar', { d: store.statsDays }),
        'ink'
      ),
      row(
        'feedback',
        urgentOpen.value > 0
          ? t('feedback.dashboard.kpi.urgentShort', { n: urgentOpen.value })
          : t('feedback.dashboard.kpi.untriagedShort', {
              n: feedback.value?.total.unassigned ?? 0,
            }),
        urgentOpen.value > 0 ? t('feedback.dashboard.kpi.urgent') : t('feedback.dashboard.kpi.untriaged'),
        urgentOpen.value > 0 ? 'warn' : 'ink'
      ),
      row('usage', shortTokens(usage.value?.totals.tokens), t('feedback.dashboard.usage.tokens'), 'ink'),
      row(
        'platform',
        platform.value?.health?.overall === 'healthy'
          ? t('feedback.dashboard.health.healthy')
          : platform.value?.health?.overall === 'degraded'
            ? t('feedback.dashboard.health.degraded')
            : '—',
        t('feedback.dashboard.health.title'),
        platform.value?.health?.overall === 'healthy'
          ? 'ok'
          : platform.value?.health?.overall === 'degraded'
            ? 'danger'
            : 'ink'
      ),
      row('performance', slowestP95.value, t('feedback.dashboard.perf.slowest'), 'ink'),
      row(
        'integrations',
        t('feedback.dashboard.integrations.delivery.deadShort', {
          n: integrations.value?.delivery.dead_letters ?? 0,
        }),
        t('feedback.dashboard.integrations.delivery.title'),
        (integrations.value?.delivery.dead_letters ?? 0) > 0 ? 'warn' : 'ink'
      ),
    ]
  })

  /** 一键一格，给上面那条导轨按 `StatsKind` 取短值用。`pulse` 仍是数组（渲染顺序），
   *  这里只是同一批数据的按名索引。 */
  const pulseByKey = computed<Record<string, PulseRow>>(() => {
    const out: Record<string, PulseRow> = {}
    for (const row of pulse.value) out[row.key] = row
    return out
  })

  /** 页签的 `title`：口径提示句 + 那一类的更新时刻（§10.4 —— 常驻年龄行是噪音，但
   *  指针停上去时「这份数据是什么时候的」要拿得到）。 */
  const titles = computed<Record<string, string>>(() => {
    const out: Record<string, string> = {}
    for (const k of KINDS) {
      const hint = pulseByKey.value[k]?.hint ?? ''
      const at = store.statsAt[k]
      out[k] = at === null ? hint : `${hint} · ${t('feedback.dashboard.updatedAt', { time: hhmm(at) })}`
    }
    return out
  })

  /** 页头那句「更新于 HH:MM」。当前类还没成功拉到过（`statsAt` 为 null）时是空串；
   *  超过 5 分钟追加「· N 分钟前」—— 切回旧类时一眼看出这份数据有多旧。 */
  const stampText = computed(() => {
    const at = store.statsAt[kind.value]
    if (at === null) return ''
    const ageMin = Math.floor((Date.now() - at) / 60_000)
    return ageMin >= 5
      ? t('feedback.dashboard.updatedAtStale', { time: hhmm(at), n: ageMin })
      : t('feedback.dashboard.updatedAt', { time: hhmm(at) })
  })

  /** 迷你列表的行。「需处理」只有两种状态（还没人接手、有人在做）；更新时间取
   *  `last_activity_at`，为空退回 `created_at`。 */
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

  /** 反馈那一屏右侧那条迷你列表的加载态（它走的是 `loadAdmin`，不在 `loadStats` 里）。 */
  const listLoading = computed(() => store.adminLoading)

  /** 切分类：先写状态（控件立刻跟上），再拉这一类。已经拉过的那一类**不重拉** ——
   *  切回来看到的是刚才那份，而「重新拉一次」是刷新按钮或轮询的事。 */
  function selectKind(next: StatsKind) {
    if (next === store.statsKind) return
    store.statsKind = next
    if (store.stats[next] === null) void store.loadStats(next, store.statsDays)
  }

  /** 换窗口。store 里那一条会把已加载的窗口类全部重拉（缓存键=窗口）。 */
  function setDays(next: StatsDays) {
    store.setStatsDays(next)
  }

  function pollTick() {
    if (document.visibilityState !== 'visible') return
    if (!POLL_KINDS.includes(kind.value)) return
    void store.loadStats(kind.value, store.statsDays)
  }

  /** 回到可见时补一次（如果这一类已经旧过一个周期）。轮询失败不打扰 —— `loadStats`
   *  失败只写 `store.error`，旧数据配旧时间戳还在，错误块只在「什么都没拿到」时整屏。 */
  function onVisibleAgain() {
    if (document.visibilityState !== 'visible') return
    if (!POLL_KINDS.includes(kind.value)) return
    const at = store.statsAt[kind.value]
    if (at !== null && Date.now() - at > POLL_MS) void store.loadStats(kind.value, store.statsDays)
  }

  /** 错误块的重试：**真重拉**当前这一类；反馈类顺带把迷你列表那一路也重拉
   *  （它走的是 `loadAdmin`，不在 `loadStats` 里）。 */
  function retry() {
    void store.loadStats(store.statsKind, store.statsDays)
    if (store.statsKind === 'feedback') void store.loadAdmin()
  }

  onMounted(() => {
    // 默认落点是**交付**（store 的 statsKind 初始值）：管理员早上第一个问题是
    // 「现在该我动的是哪几件」，不是「今天 token 多少」。用户显式选过的分类由
    // store 记着，重挂载原样恢复 —— 这里只补拉「当前这一类还没有数据」的情形。
    //
    // `loadStats` 只写 `statsBusy`、不在完成前写 `stats`，所以「切片还是 null 就拉」
    // 在首次挂载时**必然**成立一次 —— 这正是要的；但不要在上面的分支之外再补第二句，
    // 否则同一分类两个并发请求，store 的序号守卫（feedback.ts 的 `statsSeq`）会把先
    // 回来的那个响应丢掉。
    if (store.stats[store.statsKind] === null) {
      void store.loadStats(store.statsKind, store.statsDays)
    }
    // 两件事并发：队列那一路给 `counts` 和迷你列表的十行（反馈分类要），看板那一路给当前
    // 分类的曲线。串行只会让首屏多等一个来回。
    void store.loadAdmin()

    pollTimer = window.setInterval(pollTick, POLL_MS)
    document.addEventListener('visibilitychange', onVisibleAgain)
  })

  onBeforeUnmount(() => {
    window.clearInterval(pollTimer)
    document.removeEventListener('visibilitychange', onVisibleAgain)
  })

  return {
    kinds: KINDS,
    tabs: TAB_KEY,
    titles,
    // 导轨按名索引交给页头（渲染顺序由 `kinds` 决定，见 `tabs`）。
    pulse: pulseByKey,
    kind,
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
    selectKind,
    setDays,
    retry,
  }
}
