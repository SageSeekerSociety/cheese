// 模型管理页**取数**的那一半：三段各自的读、各自的失败，写操作的四处状态，窗口，
// 以及页头和 KPI 要的那几个判据。
//
// 分家的理由和 `useAdminDashboard` 是同一个形状：这一页原先一份 `<script setup>` 一千
// 四百行，取数和画法长在一起，于是三段都拿不出来单独看 —— 挂在预览站里得先立一个假
// 后端。现在三件事各归各位：
//
//   - 取数（三条接口、窗口、每段自己的加载与失败、写操作的状态）→ 这里；
//   - 画（表、额度、审计、页头、那条横条）→ `components/admin/models/*.vue`，只吃
//     props、只往上发事件；
//   - 接线（哪一段点哪一下调哪个动作）→ 页面自己。
//
// **三条读各拉各的、各失败各的**（`load` 只负责模型那一段）不是随手：额度那一段挂了
// 不该把模型表也一起清空，审计读不到不该显示「暂无操作」——
// 三种失败在这一页是三句话，由三段各自说，页顶那条横条只说**写**失败。
import type { ModelFormPayload } from '@/components/admin/AdminModelFormDialog.vue'
import type { ModelTier } from '@/lib/adminCredits'
import type { AuditItem, ModelRow, ModelsListing, ModelUsage, ProjectRow, ProjectsPayload } from '@/lib/adminModels'

import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { useAdminModelDetail } from '@/composables/useAdminModelDetail'

import {
  createGatewayModel,
  deleteGatewayModel,
  getGatewayAudit,
  getGatewayModels,
  getGatewayProjects,
  setGatewayModelBlocked,
  setGatewayProjectBudget,
  updateGatewayModel,
} from '@/api'
import { setGatewayModelTier } from '@/api/adminCredits'
import { relTime } from '@/lib/relTime'
import { fmtCost, fmtNum, fmtSI } from '@/lib/usageFormat'

/** 审计那一段最多拉几条。后端默认 50，和这里的 50 是同一个数。 */
const AUDIT_LIMIT = 50

/** 页头的三个窗口档位。三段共用，所以窗口是**页面**的问题，不是某一段的问题。 */
const WINDOWS = [7, 14, 30]

export function useAdminModels() {
  const { t } = useI18n()

  /** 窗口。三段共用。 */
  const days = ref(7)

  const models = ref<ModelsListing | null>(null)
  const projects = ref<ProjectsPayload | null>(null)
  const audit = ref<AuditItem[]>([])

  const loading = ref(true)
  const projectsLoading = ref(false)
  const auditLoading = ref(false)

  /** 主列表（模型）加载失败的原话。页面直接显示它，不另写一句「加载失败」。 */
  const loadError = ref<string | null>(null)
  /** 额度段读失败的原话。和主列表分开：这一段自己拉、也自己失败，读不到时**不能**退化成
   *  一张空表 —— 空表说的是「还没有项目」，而这里发生的是「没读到」。 */
  const projectsError = ref<string | null>(null)
  /** 最近操作段读失败的原话。同上：读不到时不能显示「暂无操作」。 */
  const auditError = ref<string | null>(null)
  /** 写操作（删除 / 停用 / 改额度）失败的原话。和加载失败分开：一次写失败不该让整页
   *  看起来像没加载出来，重试的也不是同一件事。 */
  const writeError = ref<string | null>(null)
  const notice = ref<string | null>(null)

  const formOpen = ref(false)
  const formMode = ref<'add' | 'edit'>('add')
  const formSeed = ref<ModelRow | null>(null)
  const formSaving = ref(false)
  const formError = ref<string | null>(null)

  const drawerOpen = ref(false)
  const drawerName = ref<string | null>(null)

  // 详情抽屉那一半的数据。抽屉的画面在页面的视图里（只吃 props），取数留在这里 —— 它跟着
  // 页面的窗口走（窗口变了重拉），而抽屉自己单独用时走的是同一份
  // `useAdminModelDetail`，两处不会分家。
  const {
    detail: drawerDetail,
    loading: drawerDetailLoading,
    error: drawerDetailError,
    load: loadDetail,
  } = useAdminModelDetail({
    open: () => drawerOpen.value,
    name: () => drawerName.value,
    days: () => days.value,
  })

  // 详情抽屉自己关上（右上的叉 / Esc / 点外面）。原来这一段由抽屉组件内部管，页面拆开之后
  // 开合是页面的事：关掉只是把 `drawerOpen` 落回 false，`drawerName` 留着（下次打开的是
  // 同一个模型时不用重设）。
  function closeDetail() {
    drawerOpen.value = false
  }

  /** 审计区展开「查看改动」的行（按下标记）。diff 在子组件 `AdminAuditDiff` 里画。 */
  const auditExpanded = ref<Set<number>>(new Set())

  const budgetOpen = ref(false)
  const budgetProject = ref<ProjectRow | null>(null)
  const budgetSaving = ref(false)
  const budgetError = ref<string | null>(null)

  /** 删除 / 停用都要过一道确认：它们当场改的是全平台的路由，按钮又在一行名字旁边。 */
  const deleteTarget = ref<ModelRow | null>(null)
  const deleting = ref(false)
  const blockTarget = ref<ModelRow | null>(null)
  const blocking = ref(false)

  /** 服务端的原话在 `message` 里（`ApiError` 带上来的）。取不到才用兜底那句。 */
  function message(e: unknown, fallback: string): string {
    return e instanceof Error && e.message ? e.message : fallback
  }

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

  /* ---- 读 ---- */

  async function load() {
    loading.value = true
    loadError.value = null
    try {
      models.value = (await getGatewayModels(days.value)) as unknown as ModelsListing
    } catch (e) {
      models.value = null
      loadError.value = message(e, t('models.page.loadFailed'))
    } finally {
      loading.value = false
    }
    // 另外两段各拉各的、各画各的失败：额度那一段挂了不该把模型表也一起清空。
    void loadProjects()
    void loadAudit()
  }

  async function loadProjects() {
    projectsLoading.value = true
    projectsError.value = null
    try {
      projects.value = (await getGatewayProjects(days.value)) as unknown as ProjectsPayload
    } catch (e) {
      // 失败**不能**退化成一张空表：空表说的是「还没有项目」，而这里发生的是「没读到」。
      projects.value = null
      projectsError.value = message(e, t('models.page.loadFailed'))
    } finally {
      projectsLoading.value = false
    }
  }

  async function loadAudit() {
    auditLoading.value = true
    auditError.value = null
    try {
      audit.value = ((await getGatewayAudit(AUDIT_LIMIT)) as unknown as { items: AuditItem[] }).items ?? []
    } catch (e) {
      // 同上：读不到时不能显示「暂无操作」，那是把「没读到」说成了「没有」。
      audit.value = []
      auditError.value = message(e, t('models.page.loadFailed'))
    } finally {
      auditLoading.value = false
    }
  }

  function changeWindow(value: number) {
    if (days.value === value) return
    days.value = value
    models.value = null
    projects.value = null
    void load()
  }

  /** 页顶那条横条上的叉：横条同一时刻只画一条（写失败与提示互斥），所以两支一起清，
   *  效果和「清掉正在显示的那一条」逐字相同，页面上不用再分辨是哪一条。 */
  function clearFlash() {
    writeError.value = null
    notice.value = null
  }

  /* ---- 写 ---- */

  function openAdd() {
    formMode.value = 'add'
    formSeed.value = null
    formError.value = null
    formOpen.value = true
  }

  function openEdit(row: ModelRow) {
    formMode.value = 'edit'
    formSeed.value = row
    formError.value = null
    formOpen.value = true
  }

  function openDetail(row: ModelRow) {
    drawerName.value = row.name
    drawerOpen.value = true
  }

  async function onFormSubmit(payload: ModelFormPayload) {
    formSaving.value = true
    formError.value = null
    writeError.value = null
    try {
      if (formMode.value === 'add') {
        // add 模式表单必带 `name`（空时不放行），这里用 `?? ''` 只是把它从可选收成必填，
        // 真正的校验仍在服务端。
        await createGatewayModel({ ...payload, name: payload.name ?? '' })
        notice.value = t('models.notice.added', { name: payload.name ?? '' })
      } else {
        await updateGatewayModel(formSeed.value?.name ?? '', payload)
        notice.value = t('models.notice.updated', { name: formSeed.value?.name ?? '' })
      }
      formOpen.value = false
      await load()
    } catch (e) {
      // 失败不关框、原因照原话显示 —— 关掉框等于把刚填的一屏字和「为什么退回」一起丢掉。
      formError.value = message(e, t('models.page.saveFailed'))
    } finally {
      formSaving.value = false
    }
  }

  function askDelete(row: ModelRow) {
    writeError.value = null
    deleteTarget.value = row
  }

  /** 确认框自己关上（取消 / Esc / 点外面）：把目标清掉。原来的写法是「任何一次更新都
   *  清」，而 `v-dialog` 只会发 `false` —— 两句话说的是同一个结果，收在这里一次。 */
  function closeDelete() {
    deleteTarget.value = null
  }

  async function confirmDelete() {
    const row = deleteTarget.value
    if (!row) return
    deleting.value = true
    writeError.value = null
    try {
      await deleteGatewayModel(row.name)
      notice.value = t('models.notice.deleted', { name: row.name })
      deleteTarget.value = null
      await load()
    } catch (e) {
      writeError.value = message(e, t('models.page.deleteFailed'))
      deleteTarget.value = null
    } finally {
      deleting.value = false
    }
  }

  function askBlock(row: ModelRow) {
    writeError.value = null
    blockTarget.value = row
  }

  /** 同上一条：停用 / 启用那个框自己关上。 */
  function closeBlock() {
    blockTarget.value = null
  }

  async function confirmBlock() {
    const row = blockTarget.value
    if (!row) return
    const next = !row.blocked
    blocking.value = true
    writeError.value = null
    try {
      await setGatewayModelBlocked(row.name, next)
      notice.value = next
        ? t('models.notice.blocked', { name: row.name })
        : t('models.notice.unblocked', { name: row.name })
      blockTarget.value = null
      await load()
    } catch (e) {
      writeError.value = message(e, t('models.page.blockFailed'))
      blockTarget.value = null
    } finally {
      blocking.value = false
    }
  }

  /** 改一条模型的档位：方案按档位限定可用的模型。成功后整表重拉，失败落到写失败那条横条。 */
  async function setTier(row: ModelRow, tier: ModelTier) {
    if (row.tier === tier) return
    writeError.value = null
    try {
      await setGatewayModelTier(row.name, tier)
      await load()
    } catch (e) {
      writeError.value = message(e, t('models.table.tierFailed'))
    }
  }

  function openBudget(row: ProjectRow) {
    budgetError.value = null
    budgetProject.value = row
    budgetOpen.value = true
  }

  async function onBudgetSubmit(maxBudgetUsd: number | null) {
    const row = budgetProject.value
    if (!row) return
    budgetSaving.value = true
    budgetError.value = null
    try {
      await setGatewayProjectBudget(row.project_id, maxBudgetUsd)
      notice.value = t('models.notice.budgetSet', { name: row.name })
      budgetOpen.value = false
      await loadProjects()
    } catch (e) {
      // 框里的错误留在框里（同表单）：改的是别的项目的刹车值，重开一次要重新找那一行。
      budgetError.value = message(e, t('models.page.budgetFailed'))
    } finally {
      budgetSaving.value = false
    }
  }

  function toggleAuditDiff(index: number) {
    const next = new Set(auditExpanded.value)
    if (next.has(index)) next.delete(index)
    else next.add(index)
    auditExpanded.value = next
  }

  onMounted(load)

  return {
    // 窗口与页头
    days,
    windows: WINDOWS,
    subLine,
    health,
    // 三段的数据与各自的读
    models,
    projects,
    audit,
    loading,
    projectsLoading,
    auditLoading,
    loadError,
    projectsError,
    auditError,
    load,
    loadProjects,
    loadAudit,
    changeWindow,
    // 判据
    gatewayDown,
    gatewayDetail,
    modelsState,
    projectsState,
    kpis,
    num,
    projectTotalsText,
    // 页顶那条横条
    writeError,
    notice,
    clearFlash,
    // 模型段的写
    formOpen,
    formMode,
    formSeed,
    formSaving,
    formError,
    openAdd,
    openEdit,
    onFormSubmit,
    deleteTarget,
    deleting,
    askDelete,
    closeDelete,
    confirmDelete,
    blockTarget,
    blocking,
    askBlock,
    closeBlock,
    confirmBlock,
    setTier,
    // 详情抽屉
    drawerOpen,
    drawerName,
    drawerDetail,
    drawerDetailLoading,
    drawerDetailError,
    loadDetail,
    closeDetail,
    openDetail,
    // 额度段
    budgetOpen,
    budgetProject,
    budgetSaving,
    budgetError,
    openBudget,
    onBudgetSubmit,
    // 审计段
    auditExpanded,
    toggleAuditDiff,
  }
}
