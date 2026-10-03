// 方案与额度页（`/admin/credits`）的取数与动作。画法在 `AdminCreditsPageView` 和
// `components/admin/credits/*`，这里只管：拉什么、什么时候拉、写完之后哪一块要刷新。
import type {
  CreditAudit,
  CreditTeamDetail,
  CreditTeamPage,
  CreditTeamRow,
  GrantInput,
  ModelTier,
  Plan,
  PlanInput,
  TeamKind,
} from '@/lib/adminCredits'

import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import {
  createPlan,
  deletePlan,
  getCreditTeam,
  getCreditTeamHistory,
  grantTeamCredits,
  listCreditTeams,
  listPlanModels,
  listPlans,
  setCreditTeamPlan,
  updatePlan,
} from '@/api/adminCredits'

const PAGE_SIZE = 20
const SEARCH_DEBOUNCE_MS = 300

export function useAdminCredits() {
  const { t } = useI18n()

  /** 服务端的原话在 `message` 里；取不到才用兜底那句。 */
  function message(e: unknown, fallback: string): string {
    return e instanceof Error && e.message ? e.message : fallback
  }

  // ---- 方案 ----
  const plans = ref<Plan[] | null>(null)
  const plansLoading = ref(false)
  const plansError = ref<string | null>(null)

  async function loadPlans() {
    plansLoading.value = true
    plansError.value = null
    try {
      plans.value = (await listPlans()).plans
    } catch (e) {
      plans.value = null
      plansError.value = message(e, t('credits.plans.loadFailed'))
    } finally {
      plansLoading.value = false
    }
  }

  /** 每一档有哪些模型：方案对话框里勾选框旁的说明。读不到就不说明。 */
  const tierModels = ref<Partial<Record<ModelTier, string[]>>>({})

  async function loadTierModels() {
    try {
      const listing = await listPlanModels()
      const byTier: Partial<Record<ModelTier, string[]>> = { included: [], premium: [], frontier: [] }
      for (const model of listing.models) {
        byTier[model.tier]?.push(model.label || model.id)
      }
      tierModels.value = byTier
    } catch {
      tierModels.value = {}
    }
  }

  const planDialogOpen = ref(false)
  const planEditing = ref<Plan | null>(null)
  const planSaving = ref(false)
  const planSaveError = ref<string | null>(null)

  function openPlan(plan: Plan | null) {
    planEditing.value = plan
    planSaveError.value = null
    planDialogOpen.value = true
  }

  /** 删掉正在编辑的方案；有团队在用或是默认方案时服务端会拒绝，原话显示在框里。 */
  async function removePlan() {
    const plan = planEditing.value
    if (!plan) return
    planSaving.value = true
    planSaveError.value = null
    try {
      await deletePlan(plan.key)
      planDialogOpen.value = false
      await loadPlans()
    } catch (e) {
      planSaveError.value = message(e, t('credits.planDialog.deleteFailed'))
    } finally {
      planSaving.value = false
    }
  }

  async function savePlan(input: PlanInput) {
    planSaving.value = true
    planSaveError.value = null
    try {
      if (planEditing.value) await updatePlan(planEditing.value.key, input)
      else await createPlan(input)
      planDialogOpen.value = false
      await loadPlans()
    } catch (e) {
      planSaveError.value = message(e, t('credits.planDialog.saveFailed'))
    } finally {
      planSaving.value = false
    }
  }

  // ---- 团队 ----
  const query = ref('')
  const planFilter = ref<string | null>(null)
  const kindFilter = ref<TeamKind | null>(null)
  const page = ref(1)
  const teams = ref<CreditTeamPage | null>(null)
  const teamsLoading = ref(false)
  const teamsError = ref<string | null>(null)
  /** 只认最后一次请求的回答：打字快时先发的那次可能后到。 */
  let teamsRequest = 0

  async function loadTeams() {
    const seq = ++teamsRequest
    teamsLoading.value = true
    teamsError.value = null
    try {
      const result = await listCreditTeams({
        q: query.value.trim() || undefined,
        plan: planFilter.value ?? undefined,
        kind: kindFilter.value ?? undefined,
        page: page.value,
        pageSize: PAGE_SIZE,
      })
      if (seq !== teamsRequest) return
      teams.value = result
    } catch (e) {
      if (seq !== teamsRequest) return
      teams.value = null
      teamsError.value = message(e, t('credits.teams.loadFailed'))
    } finally {
      if (seq === teamsRequest) teamsLoading.value = false
    }
  }

  let searchTimer: ReturnType<typeof setTimeout> | undefined

  function setQuery(value: string) {
    query.value = value
    page.value = 1
    clearTimeout(searchTimer)
    searchTimer = setTimeout(() => void loadTeams(), SEARCH_DEBOUNCE_MS)
  }

  function setPlanFilter(value: string | null) {
    planFilter.value = value
    page.value = 1
    void loadTeams()
  }

  function setKindFilter(value: TeamKind | null) {
    kindFilter.value = value
    page.value = 1
    void loadTeams()
  }

  function setPage(value: number) {
    page.value = value
    void loadTeams()
  }

  /** 在搜索或筛选：空态说「没有匹配的」而不是「还没有团队」。 */
  const searching = computed(() => query.value.trim() !== '' || planFilter.value !== null || kindFilter.value !== null)

  // ---- 一个团队 ----
  const panelOpen = ref(false)
  const team = ref<CreditTeamDetail | null>(null)
  const teamLoading = ref(false)
  const teamError = ref<string | null>(null)
  const history = ref<CreditAudit[] | null>(null)
  const historyError = ref<string | null>(null)
  const teamPlanSaving = ref(false)
  const teamPlanError = ref<string | null>(null)
  let openTeamId: number | null = null

  async function loadTeam(id: number) {
    teamLoading.value = true
    teamError.value = null
    try {
      const detail = await getCreditTeam(id)
      if (openTeamId === id) team.value = detail
    } catch (e) {
      if (openTeamId === id) teamError.value = message(e, t('credits.panel.loadFailed'))
    } finally {
      if (openTeamId === id) teamLoading.value = false
    }
  }

  async function loadHistory(id: number) {
    historyError.value = null
    try {
      const items = (await getCreditTeamHistory(id)).items
      if (openTeamId === id) history.value = items
    } catch (e) {
      if (openTeamId === id) historyError.value = message(e, t('credits.panel.historyFailed'))
    }
  }

  function openTeam(row: CreditTeamRow) {
    openTeamId = row.id
    team.value = null
    history.value = null
    teamPlanError.value = null
    panelOpen.value = true
    void loadTeam(row.id)
    void loadHistory(row.id)
  }

  function retryTeam() {
    if (openTeamId !== null) void loadTeam(openTeamId)
  }

  async function changeTeamPlan(planKey: string) {
    const current = team.value
    if (!current || planKey === current.plan.key) return
    teamPlanSaving.value = true
    teamPlanError.value = null
    try {
      await setCreditTeamPlan(current.id, planKey)
      await Promise.all([loadTeam(current.id), loadHistory(current.id), loadTeams()])
    } catch (e) {
      teamPlanError.value = message(e, t('credits.panel.planFailed'))
    } finally {
      teamPlanSaving.value = false
    }
  }

  const grantOpen = ref(false)
  const grantSaving = ref(false)
  const grantError = ref<string | null>(null)

  function openGrant() {
    grantError.value = null
    grantOpen.value = true
  }

  async function grant(input: GrantInput) {
    const current = team.value
    if (!current) return
    grantSaving.value = true
    grantError.value = null
    try {
      await grantTeamCredits(current.id, input)
      grantOpen.value = false
      await Promise.all([loadTeam(current.id), loadHistory(current.id), loadTeams()])
    } catch (e) {
      grantError.value = message(e, t('credits.grantDialog.failed'))
    } finally {
      grantSaving.value = false
    }
  }

  onMounted(() => {
    void loadPlans()
    void loadTeams()
    void loadTierModels()
  })

  onBeforeUnmount(() => clearTimeout(searchTimer))

  return {
    plans,
    plansLoading,
    plansError,
    loadPlans,
    tierModels,
    planDialogOpen,
    planEditing,
    planSaving,
    planSaveError,
    openPlan,
    savePlan,
    removePlan,
    query,
    planFilter,
    kindFilter,
    setPlanFilter,
    setKindFilter,
    teams,
    teamsLoading,
    teamsError,
    searching,
    setQuery,
    setPage,
    loadTeams,
    panelOpen,
    team,
    teamLoading,
    teamError,
    history,
    historyError,
    teamPlanSaving,
    teamPlanError,
    openTeam,
    retryTeam,
    changeTeamPlan,
    grantOpen,
    grantSaving,
    grantError,
    openGrant,
    grant,
  }
}
