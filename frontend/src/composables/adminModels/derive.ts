// 页头和横条要的那几个判据与读数，全是从取到的东西上读出来的，不碰网络。
//
// 三段各画各的，但「这一段该画 rows 还是 error」「顶上那几张数」「页头那句说明」是
// **页面**的口径，所以它们在这里一处写：判据只写一遍，页头和表体才不会各说一句。
import type { Ref } from 'vue'
import type { ModelsListing, ModelUsage, ProjectsPayload } from '@/lib/adminModels'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import { relTime } from '@/lib/relTime'
import { fmtCost, fmtNum, fmtSI } from '@/lib/usageFormat'

export interface AdminModelsDeriveHost {
  models: Ref<ModelsListing | null>
  projects: Ref<ProjectsPayload | null>
  /** 模型那一段失败的原话：网关不可达和它一起决定表壳画 rows 还是 error。 */
  loadError: Ref<string | null>
  /** 额度那一段失败的原话。 */
  projectsError: Ref<string | null>
}

export function useAdminModelsDerive(host: AdminModelsDeriveHost) {
  const { t } = useI18n()
  const { models, projects, loadError, projectsError } = host

  const gateway = computed(() => models.value?.gateway ?? null)
  /** 网关不可达 / 没配管理密钥 —— 两种都不该画一张空表当成「还没有模型」。 */
  const gatewayDown = computed(
    () => gateway.value !== null && (!gateway.value.reachable || !gateway.value.admin_configured)
  )

  /** 网关读不出来时那句解释。**两种原因是两种修法**（去把网关起起来 / 去配管理密钥），
   *  所以分开说；页头的健康灯与表格里那条说明共用这一份判据，不各写一遍。 */
  const gatewayDetail = computed(() =>
    gateway.value?.admin_configured ? t('models.page.gateway.unreachable') : t('models.page.gateway.unconfigured')
  )

  /** 表壳的三态（`rows` / `error`）。读不到时**不退化成空的表体**（空表说的是「还没有
   *  模型」，而这里发生的是「没读到」），网关不可达也归到这一类：表里列的每一行都来自
   *  网关，网关不答话就没有可读的东西 —— 画一张「暂无模型」是把这个事实说反了。 */
  const modelsState = computed<'rows' | 'error'>(() => (loadError.value || gatewayDown.value ? 'error' : 'rows'))

  /** 额度段同上一句：这一段的失败只由它自己那次读决定（网关挂了它照样会失败，
   *  那时 `projectsError` 有值；网关挂了但这一段读到了，就该照画）。 */
  const projectsState = computed<'rows' | 'error'>(() => (projectsError.value ? 'error' : 'rows'))

  /** 页头的 readiness 健康灯：常在的一眼状态（`gatewayDown` 那条警告负责解释，
   *  灯负责让人不看警告也知道网关活没活）。初始未加载不画 —— 「还没读到」不是
   *  一种健康状态。 */
  const health = computed<{ ok: boolean; text: string; title: string } | null>(() => {
    const g = gateway.value
    if (!g) return null
    if (g.reachable && g.admin_configured && g.readiness) {
      const fetched = relTime(g.fetched_at)
      return {
        ok: true,
        text: fetched ? `${g.readiness} · ${fetched}` : g.readiness,
        title: g.detail ?? g.readiness,
      }
    }
    // 灯上只留一句短的（「网关读不到」）：完整那句由表格里那条说明来说，两处写同一句
    // 话会让人以为是两次失败。灯这一格把它挂在 title 上，指着看的人还是拿得到全文。
    const detail = g.detail ?? gatewayDetail.value
    return { ok: false, text: t('models.health.down'), title: detail }
  })

  const totals = computed<ModelUsage | null>(() => models.value?.totals ?? null)

  const offeredCount = computed(() => (models.value?.models ?? []).filter((m) => m.offered).length)

  /** 拿不到（`null`）给空串，卡片自己画成 `—`；给 0 的话「没读到」和「确实是零」就分不开。 */
  function num(v: number | null | undefined): string {
    return v === null || v === undefined ? '' : fmtNum(v)
  }

  /** 顶上那几张数。和看板同一套卡（`AdminKpiCard`），不另外造一种卡。 */
  const kpis = computed(() => [
    { key: 'models', label: t('models.kpi.models'), value: num(models.value?.models.length) },
    { key: 'offered', label: t('models.kpi.offered'), value: num(models.value?.models ? offeredCount.value : null) },
    { key: 'spend', label: t('models.kpi.spend'), value: totals.value ? fmtCost(totals.value.spend_usd) : '' },
    // token 与调用数是这一页的主指标之一：钱是结果，token 才是「模型被用了多少」。
    // 缩写走 fmtSI（和后台看板同一个台阶），精确值在表格那一列上。
    { key: 'tokens', label: t('models.kpi.tokens'), value: totals.value ? fmtSI(totals.value.total_tokens) : '' },
    { key: 'requests', label: t('models.kpi.requests'), value: totals.value ? fmtNum(totals.value.requests) : '' },
    { key: 'failed', label: t('models.kpi.failed'), value: totals.value ? fmtNum(totals.value.failed_requests) : '' },
  ])

  const windowText = computed(() => {
    const w = models.value?.window
    if (!w) return ''
    return `${w.start_date} – ${w.end_date}`
  })

  /** 页头那句说明：这一页管什么 + 当前窗口。窗口是三段共用的，所以在页头只说一次。 */
  const subLine = computed(() =>
    windowText.value ? `${t('models.page.subtitle')} · ${windowText.value}` : t('models.page.subtitle')
  )

  const projectTotalsText = computed(() => {
    const p = projects.value?.totals
    if (!p) return ''
    return t('models.budget.summary', {
      projects: p.projects,
      withKey: p.with_key,
      over: p.over_budget,
      unlimited: p.unlimited,
    })
  })

  return {
    gatewayDown,
    gatewayDetail,
    modelsState,
    projectsState,
    health,
    kpis,
    num,
    subLine,
    projectTotalsText,
  }
}
