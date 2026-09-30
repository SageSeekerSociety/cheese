<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

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
import AdminAuditDiff from '@/components/admin/AdminAuditDiff.vue'
import AdminBudgetDialog from '@/components/admin/AdminBudgetDialog.vue'
import AdminEmptyState from '@/components/admin/AdminEmptyState.vue'
import AdminFlash from '@/components/admin/AdminFlash.vue'
import AdminGrid from '@/components/admin/AdminGrid.vue'
import AdminKpiCard from '@/components/admin/AdminKpiCard.vue'
import AdminModelDetailDrawer from '@/components/admin/AdminModelDetailDrawer.vue'
import AdminModelFormDialog, { type ModelFormPayload } from '@/components/admin/AdminModelFormDialog.vue'
import AdminModelPriceCell from '@/components/admin/AdminModelPriceCell.vue'
import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'
import AdminSparkline from '@/components/admin/AdminSparkline.vue'
import AdminTabs from '@/components/admin/AdminTabs.vue'
import UserRef from '@/components/common/UserRefLink.vue'
import { relTime } from '@/lib/relTime'
import { fmtCost, fmtNum, fmtPercent, fmtSI } from '@/lib/usageFormat'

// 管理后台的「模型管理」（`/admin/models`）。它管的是**网关那一侧的模型台账**，不是
// 平台自己声明的东西 —— 页面上的每一行都来自 `GET /model/info`，每一处改动都落回网关。
//
// 这一页有三段，顺序按「先看什么后看什么」排：
//
//   1. **模型** —— 有哪些模型、上没上架、什么价、跑了多少。这是页面的主语。
//   2. **额度** —— 给项目的网关 key 设「刹车值」。和模型分开，因为它们回答的是两个不同
//      的问题（「供给什么」和「谁在用、封顶多少」），挤在一张表里会让两件事互相稀释。
//   3. **最近操作** —— 谁改了什么。写操作是危险动作，改完要留痕，人也要能回看。
//
// 四个数字摆在最上面（模型数 / 已上架 / 窗口花费 / 失败调用）：读的人先知道「要不要动手」，
// 再往下逐段看。窗口是页头的一个选择器，三段共用 —— 窗口在页面级只问一次。
//
// **config 来源的模型是只读的**（网关拒绝改它）：这一页不给它编辑 / 删除 / 停用的按钮，
// 说明写在那一格上（「只读」），而不是画一个点了报错的按钮。上架开关也不在这里 ——
// 它属于「编辑」，无价时灰掉的那条闸门在表单里（模型「上架」和「单价」是同一件事的两面，
// 分开画会让人以为它们互不相干）。
//
// 写操作失败一律**在页面上显示服务端原话**（契约 §4）：表单失败时表单不关，页面上那种
// 确认框失败时留在页顶那条错误里。一句「操作失败」会把这页最需要的东西 —— 为什么失败 —— 丢掉。

/** 一个有价格模型在某个窗口里的用量（§3.1 的 `usage`）。 */
interface Usage {
  spend_usd: number
  requests: number
  failed_requests: number
  total_tokens: number
}

/** §3.1 的模型项。 */
interface ModelRow {
  name: string
  label: string
  origin: string
  blocked: boolean
  selectable: boolean
  priced: boolean
  offered: boolean
  blocked_reason: string | null
  unpriced_reason: string | null
  upstream: { model: string; host: string | null; provider: string }
  prices: Record<string, number | null | undefined>
  capabilities: Record<string, boolean | undefined>
  usage: Usage
  /** 行内 sparkline 的逐日 token（与详情折线同源同账）。 */
  series?: number[]
}

interface GatewayState {
  reachable: boolean
  readiness: string | null
  admin_configured: boolean
  detail: string | null
  fetched_at: string
}

interface ModelsListing {
  gateway: GatewayState
  window: { days: number; start_date: string; end_date: string }
  totals: Usage
  models: ModelRow[]
}

interface ProjectRow {
  project_id: string
  name: string
  key_alias: string
  has_key: boolean
  gateway_spend_usd: number
  max_budget_usd: number | null
  budget_derived_usd: number | null
  budget_override_usd: number | null
  credits: { total: number | null; used: number; remaining: number; unlimited: boolean }
  usage: Usage
}

interface ProjectsPayload {
  projects: ProjectRow[]
  totals: { projects: number; with_key: number; over_budget: number; unlimited: number }
}

interface AuditItem {
  created_at: string
  actor_handle: string
  action: string
  target: string
  result: string
  detail: string | null
  /** 改动前后的字段快照（写入时已脱敏）。「查看改动」按钮与 diff 展开的数据。 */
  before: Record<string, unknown> | null
  after: Record<string, unknown> | null
}

const { t } = useI18n()

/** 窗口。三段共用，所以它是**页面**的问题，不是某一段的问题。 */
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

/** 审计区展开「查看改动」的行（按下标记）。diff 在子组件 `AdminAuditDiff` 里画。 */
const auditExpanded = ref<Set<number>>(new Set())

function toggleAuditDiff(index: number) {
  const next = new Set(auditExpanded.value)
  if (next.has(index)) next.delete(index)
  else next.add(index)
  auditExpanded.value = next
}

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

/** 来源徽章的两态：配置文件 / 运行时新增。 */
function originKey(row: ModelRow): string {
  return row.origin === 'config' ? 'models.table.origin.config' : 'models.table.origin.runtime'
}

/** 状态列的失败率：0 请求画 `—`（「没用到」和「没失败」是两句话）；>5% 红、
 *  >0 琥珀、否则灰 —— 失败率是一个**例外状态**，正常时它不该抢眼。 */
function failRate(row: ModelRow): { text: string; title: string; tone: string } {
  const { requests, failed_requests: failed } = row.usage
  if (!requests) return { text: '—', title: '', tone: 'amd__rate--muted' }
  const rate = fmtPercent(failed / requests)
  const tone = failed / requests > 0.05 ? 'amd__rate--danger' : failed > 0 ? 'amd__rate--warn' : 'amd__rate--muted'
  return { text: rate, title: t('models.table.failRate', { rate }), tone }
}

/** 状态列在窄屏卡片里**整格收起来**的条件：这个窗口里没有请求时，
 *  那一格画的是一句「—」。卡片上多一行「状态 —」是没有信息的行，而真有事的那几行
 *  照样画得出来（同成员页「异常才说话」那条）。 */
function statusQuiet(row: ModelRow): boolean {
  return !row.usage.requests
}

const totals = computed<Usage | null>(() => models.value?.totals ?? null)

const offeredCount = computed(() => (models.value?.models ?? []).filter((m) => m.offered).length)

/** 顶上四个数。和看板同一套卡（`AdminKpiCard`），不另外造一种卡。 */
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

/** 拿不到（`null`）给空串，卡片自己画成 `—`；给 0 的话「没读到」和「确实是零」就分不开。 */
function num(v: number | null | undefined): string {
  return v === null || v === undefined ? '' : fmtNum(v)
}

const windowText = computed(() => {
  const w = models.value?.window
  if (!w) return ''
  return `${w.start_date} – ${w.end_date}`
})

/** 页头那句说明：这一页管什么 + 当前窗口。窗口是三段共用的，所以在页头只说一次。 */
const subLine = computed(() =>
  windowText.value ? `${t('models.page.subtitle')} · ${windowText.value}` : t('models.page.subtitle')
)

/** 页头的窗口页签。值走字符串 —— `AdminTabs` 是 `T extends string` 的泛型；回到
 *  `changeWindow` 里再收成数字，`days` 是发给接口的参数，别让它变成字符串。 */
const windowOptions = computed(() =>
  [7, 14, 30].map((n) => ({ value: String(n), label: t('models.page.days', { n }) }))
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

/** 模型的显示名。`label` 缺失时退回 `name`（网关里的人给名字时才带 label）。 */
function displayName(row: ModelRow): string {
  return row.label || row.name
}

/** 审计动作 → 词条。**写成字面量表**，不在模板里拼 `models.audit.action.${action}` ——
 *  拼出来的键在源码里没有一处字面量出现，`catalog.spec.ts` 会把它们判成死词条。 */
const AUDIT_ACTION_KEY: Record<string, string> = {
  'model.add': 'models.audit.action.add',
  'model.update': 'models.audit.action.update',
  'model.delete': 'models.audit.action.delete',
  'model.blocked': 'models.audit.action.blocked',
  'project.budget': 'models.audit.action.budget',
  'subscription.start': 'models.audit.action.subscriptionStart',
  'subscription.complete': 'models.audit.action.subscriptionComplete',
  'subscription.cancel': 'models.audit.action.subscriptionCancel',
  'subscription.refresh': 'models.audit.action.subscriptionRefresh',
  'subscription.revoke': 'models.audit.action.subscriptionRevoke',
  'subscription.update_upstream': 'models.audit.action.subscriptionUpdateUpstream',
}

function auditActionLabel(action: string): string {
  return t(AUDIT_ACTION_KEY[action] ?? 'models.audit.action.other')
}

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
    audit.value = ((await getGatewayAudit(50)) as unknown as { items: AuditItem[] }).items ?? []
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

onMounted(load)
</script>

<template>
  <div class="amd admin-page">
    <div class="amd__inner admin-page__col page-container--admin">
      <AdminPageHeader :title="t('models.page.title')" :sub="subLine">
        <template #tools>
          <!-- readiness 健康灯：常在的一眼状态，点与文字，完整 detail 挂 title。
               触屏没有 hover —— 读不出来时表格里那条说明会给出原因（和这里的
               `health` 同一份判据），灯坏了的人不至于只能盯着一个小点猜。 -->
          <span
            v-if="health"
            class="amd__health"
            role="status"
            :aria-label="t('models.health.label')"
            :title="health.title"
          >
            <span class="amd__healthdot" :class="health.ok ? 'amd__dot--ok' : 'amd__dot--danger'" aria-hidden="true" />
            <span class="amd__healthtext t-meta-read">{{ health.text }}</span>
          </span>
          <!-- 窗口是三段共用的，所以它摆在页头（页面级），不塞进某一段的工具条里。 -->
          <AdminTabs
            size="sm"
            :label="t('models.page.window')"
            :model-value="String(days)"
            :options="windowOptions"
            @update:model-value="changeWindow(Number($event))"
          />
          <v-btn
            icon="mdi-refresh"
            variant="text"
            size="small"
            :aria-label="t('models.page.refresh')"
            :loading="loading"
            @click="load"
          />
        </template>
      </AdminPageHeader>

      <div class="amd__body admin-page__body">
        <!-- 写失败 / 提示：token 画的一条横条（`v-alert` 那套默认样在这一页像另一个
             产品）。**读失败不在这里说** —— 那一条画在各自那一段的位置上（表的列头
             下面、审计那张卡里），同一次失败说两遍，人会以为是两次。 -->
        <AdminFlash
          v-if="writeError"
          tone="error"
          :text="writeError"
          :dismiss-aria="t('models.notice.dismiss')"
          @dismiss="writeError = null"
        />
        <AdminFlash
          v-else-if="notice"
          tone="ok"
          :text="notice"
          :dismiss-aria="t('models.notice.dismiss')"
          @dismiss="notice = null"
        />

        <div class="amd__kpis">
          <AdminKpiCard v-for="kpi in kpis" :key="kpi.key" :label="kpi.label" :value="kpi.value" :loading="loading" />
        </div>

        <!-- 模型段。页面上唯一的主操作（新增模型）在这一段，所以琥珀只出现在这里一处。 -->
        <section class="amd__section">
          <div class="amd__sectiontools">
            <h2 class="amd__sectionlabel t-title">{{ t('models.page.section.models') }}</h2>
            <span class="amd__count t-meta-read">{{ num(models?.models.length) }}</span>
            <div class="amd__spacer" />
            <v-btn color="primary" size="small" prepend-icon="mdi-plus" :disabled="gatewayDown" @click="openAdd">
              {{ t('models.page.add') }}
            </v-btn>
          </div>

          <div class="amd__gridwrap">
            <AdminGrid
              :label="t('models.table.label')"
              :cols="[null, '96px', '150px', '210px', '180px', '110px', '140px']"
              :bone-widths="['64%', '54%', '70%', '58%', '62%', '50%', '46%']"
              :loading="loading && !models"
              :skeleton-rows="6"
              :state="modelsState"
              cards
              :empty="models && !models.models.length ? t('models.table.empty') : null"
            >
              <template #head>
                <tr>
                  <th scope="col">{{ t('models.table.column.name') }}</th>
                  <th scope="col">{{ t('models.table.column.origin') }}</th>
                  <th scope="col">{{ t('models.table.column.price') }}</th>
                  <th scope="col">{{ t('models.table.column.usage') }}</th>
                  <th scope="col">{{ t('models.table.column.offered') }}</th>
                  <th scope="col">{{ t('models.table.column.status') }}</th>
                  <th scope="col" class="amd__num">{{ t('models.table.column.actions') }}</th>
                </tr>
              </template>

              <!-- 读不到网关：一条平静的说明画在列头下面，重试就在旁边。接口失败和
               「网关没配管理密钥」在这里合成**一处** —— 对读的人是同一个结果：
               这张表读不出来。 -->
              <template #error>
                <AdminEmptyState
                  compact
                  tone="error"
                  :title="loadError ?? gatewayDetail"
                  :action="t('models.page.retry')"
                  @action="load"
                />
              </template>

              <template #empty>
                <AdminEmptyState compact :title="t('models.table.empty')" />
              </template>

              <tr v-for="row in models?.models ?? []" :key="row.name" class="amd__row">
                <td class="amd__cell" data-card="primary">
                  <!-- 名字本身是打开详情的入口：整行只有一个可聚焦的东西，读屏不会在一行里
                   听两遍同一个目的地。 -->
                  <button type="button" class="amd__name" @click="openDetail(row)">
                    <span class="amd__nameMain">{{ displayName(row) }}</span>
                    <span v-if="row.label" class="amd__nameSlug t-meta-read">{{ row.name }}</span>
                  </button>
                </td>
                <td class="amd__cell" :data-label="t('models.table.column.origin')">
                  <span class="amd__tag">{{ t(originKey(row)) }}</span>
                </td>
                <td class="amd__cell" :data-label="t('models.table.column.price')">
                  <AdminModelPriceCell :priced="row.priced" :prices="row.prices" :reason="row.unpriced_reason" />
                </td>
                <td class="amd__cell" :data-label="t('models.table.column.usage')">
                  <span class="amd__usage">
                    <span class="amd__usageRow">
                      <span class="t-num amd__usageMain">{{ fmtCost(row.usage.spend_usd) }}</span>
                      <span class="t-meta-read amd__dim"
                        >{{ fmtNum(row.usage.requests) }} {{ t('models.table.calls') }}</span
                      >
                    </span>
                    <span class="amd__usageRow">
                      <!-- 缩写是给人一眼看的，精确值挂在 title 上 —— 这是 fmtSI 那一条约定。 -->
                      <span class="t-meta-read amd__dim" :title="fmtNum(row.usage.total_tokens)"
                        >{{ fmtSI(row.usage.total_tokens) }} {{ t('models.usage.tokens') }}</span
                      >
                      <!-- 行内 sparkline：只承担「趋势长什么样」的一眼形状（aria-hidden）。
                       逐日精确值的可访问形式是详情抽屉那张数据表（§2.7），不是给 17
                       行各塞一个 <details> —— 同一列里就有精确总数的 title。 -->
                      <span class="amd__spark" :title="t('models.table.sparklineHint')">
                        <AdminSparkline :values="row.series ?? []" :height="20" />
                      </span>
                    </span>
                  </span>
                </td>
                <td class="amd__cell" :data-label="t('models.table.column.offered')">
                  <span class="amd__usage">
                    <span v-if="row.blocked" class="amd__tag amd__tag--off">{{ t('models.table.blocked') }}</span>
                    <span v-else-if="row.offered" class="amd__tag amd__tag--on">{{
                      t('models.table.offered.on')
                    }}</span>
                    <span v-else class="amd__tag">{{ t('models.table.offered.off') }}</span>
                    <span
                      v-if="!row.offered && row.blocked_reason"
                      class="t-meta-read amd__dim amd__reason"
                      :title="row.blocked_reason"
                      >{{ row.blocked_reason }}</span
                    >
                  </span>
                </td>
                <td
                  class="amd__cell"
                  :data-card="statusQuiet(row) ? 'hide' : undefined"
                  :data-label="t('models.table.column.status')"
                >
                  <span class="amd__usage">
                    <span class="t-num" :class="failRate(row).tone" :title="failRate(row).title">{{
                      failRate(row).text
                    }}</span>
                  </span>
                </td>
                <td class="amd__cell amd__cell--actions" :data-label="t('models.table.column.actions')">
                  <!-- config 模型只读：不给按钮，给一个**能点开改法**的入口（抽屉里有 config
                   复制卡）。一句死「只读」是信息的终点，「查看改法」是起点。 -->
                  <button v-if="row.origin === 'config'" type="button" class="amd__textbtn" @click="openDetail(row)">
                    {{ t('models.table.howToEdit') }}
                  </button>
                  <template v-else>
                    <v-btn
                      icon="mdi-pencil-outline"
                      variant="text"
                      size="small"
                      :aria-label="t('models.table.action.edit')"
                      @click="openEdit(row)"
                    />
                    <v-btn
                      :icon="row.blocked ? 'mdi-play-circle-outline' : 'mdi-cancel'"
                      variant="text"
                      size="small"
                      :aria-label="row.blocked ? t('models.table.action.unblock') : t('models.table.action.block')"
                      @click="askBlock(row)"
                    />
                    <v-btn
                      icon="mdi-trash-can-outline"
                      variant="text"
                      size="small"
                      :aria-label="t('models.table.action.delete')"
                      @click="askDelete(row)"
                    />
                  </template>
                </td>
              </tr>
            </AdminGrid>
          </div>
        </section>

        <!-- 额度段。一眼要看出「剩余 / 已用 / 刹车值 / 是不是不限量」四件事。 -->
        <section class="amd__section">
          <div class="amd__sectiontools">
            <h2 class="amd__sectionlabel t-title">{{ t('models.page.section.budgets') }}</h2>
            <span class="amd__count amd__summary t-meta-read">{{ projectTotalsText }}</span>
          </div>

          <div class="amd__gridwrap amd__gridwrap--short">
            <AdminGrid
              :label="t('models.budget.label')"
              :cols="['240px', '132px', '170px', '186px', '150px', '96px']"
              :bone-widths="['60%', '52%', '64%', '58%', '56%', '48%']"
              :loading="projectsLoading && !projects"
              :skeleton-rows="5"
              :state="projectsState"
              cards
              :empty="!projectsError && projects && !projects.projects.length ? t('models.budget.empty') : null"
            >
              <template #head>
                <tr>
                  <th scope="col">{{ t('models.budget.column.project') }}</th>
                  <th scope="col">{{ t('models.budget.column.spend') }}</th>
                  <th scope="col">{{ t('models.budget.column.usage') }}</th>
                  <th scope="col">{{ t('models.budget.column.credits') }}</th>
                  <th scope="col">{{ t('models.budget.column.brake') }}</th>
                  <th scope="col" class="amd__num">{{ t('models.table.column.actions') }}</th>
                </tr>
              </template>

              <!-- 读失败照原话显示、并给重试；**不**退化成空表（空表说的是「还没有项目」）。
               它和模型段各说自己的那一次失败，不由一个页面级的横幅代劳。 -->
              <template #error>
                <AdminEmptyState
                  compact
                  tone="error"
                  :title="projectsError ?? ''"
                  :action="t('models.page.retry')"
                  @action="loadProjects"
                />
              </template>

              <template #empty>
                <AdminEmptyState compact :title="t('models.budget.empty')" />
              </template>

              <tr v-for="row in projects?.projects ?? []" :key="row.project_id" class="amd__row">
                <td class="amd__cell" data-card="primary">
                  <span class="amd__nameStatic" :title="row.name">{{ row.name }}</span>
                </td>
                <td class="amd__cell t-num" :data-label="t('models.budget.column.spend')">
                  {{ fmtCost(row.gateway_spend_usd) }}
                </td>
                <td class="amd__cell" :data-label="t('models.budget.column.usage')">
                  <span class="amd__usage">
                    <span class="t-num amd__usageMain">{{ fmtNum(row.usage.requests) }}</span>
                    <span class="t-meta-read amd__dim"
                      >{{ fmtNum(row.usage.total_tokens) }} {{ t('models.usage.tokens') }}</span
                    >
                  </span>
                </td>
                <td class="amd__cell" :data-label="t('models.budget.column.credits')">
                  <!-- 不限量是一个**结论**，不是一个大数字，所以它先说，别让读者去比 total 和 used。 -->
                  <span v-if="row.credits.unlimited" class="amd__tag">{{ t('models.budget.unlimited') }}</span>
                  <span v-else class="amd__usage">
                    <span class="t-num amd__usageMain">{{ fmtNum(row.credits.remaining) }}</span>
                    <span class="t-meta-read amd__dim">
                      {{
                        t('models.budget.used', {
                          used: fmtNum(row.credits.used),
                          total: fmtNum(row.credits.total ?? 0),
                        })
                      }}
                    </span>
                  </span>
                </td>
                <td class="amd__cell" :data-label="t('models.budget.column.brake')">
                  <span v-if="row.max_budget_usd !== null" class="amd__usage">
                    <span class="t-num amd__usageMain">{{ fmtCost(row.max_budget_usd) }}</span>
                    <span v-if="row.budget_override_usd !== null" class="t-meta-read amd__dim">
                      {{ t('models.budget.override') }}
                    </span>
                  </span>
                  <span v-else class="amd__usage">
                    <span class="amd__dim">{{ t('models.budget.none') }}</span>
                    <span v-if="row.budget_derived_usd !== null" class="t-meta-read amd__dim">
                      {{ t('models.budget.derived', { value: fmtCost(row.budget_derived_usd) }) }}
                    </span>
                  </span>
                </td>
                <td class="amd__cell amd__cell--actions" :data-label="t('models.table.column.actions')">
                  <v-btn variant="outlined" size="small" :disabled="!row.has_key" @click="openBudget(row)">
                    {{ t('models.budget.action.set') }}
                  </v-btn>
                </td>
              </tr>
            </AdminGrid>
          </div>
        </section>

        <!-- 最近操作段：写操作是危险动作，改完要留痕、要能回看。 -->
        <section class="amd__section">
          <div class="amd__sectiontools">
            <h2 class="amd__sectionlabel t-title">{{ t('models.page.section.audit') }}</h2>
          </div>

          <div class="amd__audit">
            <!-- 读失败照原话显示、并给重试；**不**显示「暂无操作」（那是把「没读到」说成
             「没有」）。它说在这一段自己的卡里，而不是页顶那条横条上。 -->
            <AdminEmptyState
              v-if="auditError"
              compact
              tone="error"
              :title="auditError"
              :action="t('models.page.retry')"
              @action="loadAudit"
            />
            <div v-else-if="auditLoading && !audit.length" class="amd__auditSkeleton">
              <v-skeleton-loader v-for="i in 4" :key="i" type="text" />
            </div>
            <p v-else-if="!audit.length" class="amd__auditEmpty t-meta-read">{{ t('models.audit.empty') }}</p>
            <ol v-else class="amd__auditRows">
              <li v-for="(item, i) in audit" :key="i" class="amd__auditRow">
                <div class="amd__auditLine">
                  <span class="amd__auditTime t-meta-read t-num">{{ relTime(item.created_at) }}</span>
                  <span class="amd__auditWho t-body"><UserRef :handle="item.actor_handle" /></span>
                  <span class="amd__auditWhat t-body">
                    {{ auditActionLabel(item.action) }}
                    <span class="amd__auditTarget t-num">{{ item.target }}</span>
                  </span>
                  <span class="t-meta-read" :class="item.result === 'ok' ? 'amd__ok' : 'amd__fail'">
                    {{ item.result === 'ok' ? t('models.audit.result.ok') : t('models.audit.result.failed') }}
                  </span>
                  <span v-if="item.detail" class="amd__auditDetail t-meta-read" :title="item.detail">{{
                    item.detail
                  }}</span>
                  <!-- 「查看改动」只在有快照可 diff 时出现：before/after 都为空的那几项
                   操作没有字段变化可看，按钮摆在那儿只会点出一句「没有变化」。 -->
                  <button
                    v-if="item.before || item.after"
                    type="button"
                    class="amd__textbtn amd__auditDiffBtn"
                    :aria-expanded="auditExpanded.has(i)"
                    @click="toggleAuditDiff(i)"
                  >
                    {{ auditExpanded.has(i) ? t('models.audit.diff.hide') : t('models.audit.diff.show') }}
                  </button>
                </div>
                <AdminAuditDiff
                  v-if="auditExpanded.has(i) && (item.before || item.after)"
                  :before="item.before"
                  :after="item.after"
                />
              </li>
            </ol>
          </div>
        </section>
      </div>
    </div>

    <AdminModelDetailDrawer v-model="drawerOpen" :name="drawerName" :days="days" />

    <AdminModelFormDialog
      v-model="formOpen"
      :mode="formMode"
      :seed="formSeed"
      :saving="formSaving"
      :error="formError"
      @submit="onFormSubmit"
    />

    <AdminBudgetDialog
      v-model="budgetOpen"
      :project="budgetProject"
      :saving="budgetSaving"
      :error="budgetError"
      @submit="onBudgetSubmit"
    />

    <!-- 删除确认。说清后果（模型从网关移除、项目再也选不到它）。 -->
    <v-dialog
      :model-value="!!deleteTarget"
      max-width="440"
      :persistent="deleting"
      @update:model-value="deleteTarget = null"
    >
      <v-card rounded="lg">
        <v-card-title class="t-dialog-title px-4 pt-4 pb-2">{{ t('models.confirm.delete.title') }}</v-card-title>
        <v-card-text class="px-4">
          {{ t('models.confirm.delete.body', { name: deleteTarget?.name ?? '' }) }}
        </v-card-text>
        <v-card-actions class="pa-4 pt-0">
          <v-spacer />
          <v-btn variant="text" :disabled="deleting" @click="deleteTarget = null">{{
            t('models.dialog.cancel')
          }}</v-btn>
          <v-btn color="primary" :loading="deleting" @click="confirmDelete">{{
            t('models.confirm.delete.confirm')
          }}</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 停用 / 启用确认。停用会把模型从选择器里摘掉，所以要说出来。 -->
    <v-dialog
      :model-value="!!blockTarget"
      max-width="440"
      :persistent="blocking"
      @update:model-value="blockTarget = null"
    >
      <v-card rounded="lg">
        <v-card-title class="t-dialog-title px-4 pt-4 pb-2">
          {{ blockTarget?.blocked ? t('models.confirm.unblock.title') : t('models.confirm.block.title') }}
        </v-card-title>
        <v-card-text class="px-4">
          {{
            blockTarget?.blocked
              ? t('models.confirm.unblock.body', { name: blockTarget?.name ?? '' })
              : t('models.confirm.block.body', { name: blockTarget?.name ?? '' })
          }}
        </v-card-text>
        <v-card-actions class="pa-4 pt-0">
          <v-spacer />
          <v-btn variant="text" :disabled="blocking" @click="blockTarget = null">{{ t('models.dialog.cancel') }}</v-btn>
          <v-btn color="primary" :loading="blocking" @click="confirmBlock">
            {{ blockTarget?.blocked ? t('models.confirm.unblock.confirm') : t('models.confirm.block.confirm') }}
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped>

.amd__kpis {
  display: grid;
  /* 六张卡，每档都**整除**：6 / 3 / 2。auto-fit 那类写法会在某些宽度上留下最后
     一张孤零零独占一行，而这几张是并列读的指标，不是排队的东西。 */
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 16px;
}

@media (max-width: 1100px) {
  .amd__kpis {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

/* 窄屏 KPI 退成两列（同看板）：一排四张在手机上每张不到 150px，字会被压破。 */
@media (max-width: 900px) {
  .amd__kpis {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

/* 窄屏的页头折行交给 `AdminPageHeader` 自己（它的工具槽是 `flex-wrap: wrap`），
   这里只管内容区的内边距收一档 —— 两页的 24px 在 390px 上占掉了 48px 宽度。 */
@media (max-width: 700px) {
  .amd__body {
    padding: 12px 16px 16px;
  }

  /* 时间窗口这一组比标题还宽（三段加起来 ~260px），而页头那一行是**一起缩**的：
     不干预的话被挤掉的是标题 —— 390px 上「模型管理」会只剩「模型…」。让工具槽
     自己占一整行，标题就还在一整行上。（`:deep` 只为了改页头那一行的折行，尺寸、
     字号、内边距都还是 `AdminPageHeader` 自己的。） */
  .amd__inner :deep(.aph__row) {
    flex-wrap: wrap;
  }

  .amd__inner :deep(.aph__tools) {
    flex: 1 1 100%;
  }
}

.amd__section {
  display: flex;
  flex-direction: column;
  margin-top: 24px;
}

.amd__sectiontools {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 8px;
  /* 下限而不是定高：额度段那句摘要（「N 个项目 · M 有密钥 …」）在 390px 下比一行还
     长，定高会让折下来的第二行顶破这条线、压过下面的内容。 */
  min-height: 40px;
  margin-bottom: 8px;
  border-bottom: 1px solid var(--line);
}

.amd__sectionlabel {
  margin: 0;
}

.amd__count {
  color: var(--muted);
}

/* 额度段那句四参数摘要在窄屏比一行还长：`min-width: 0` 让它在 flex 行里**能缩**、
   省略号收尾，而不是把文字折成两行去顶破上面那条分隔线。 */
.amd__summary {
  overflow: hidden;
  min-width: 0;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.amd__spacer {
  flex: 1 1 auto;
}

/* 表格给自己一个高度上限，让表头**在里面 sticky**：不给上限的话整张表跟着页面长，
   表头会随页面滚走，表壳那套「一屏十七行还记得第 7 列是什么」的设计就落空了。 */
.amd__gridwrap {
  display: flex;
  max-height: 56vh;
  min-height: 220px;
}

.amd__gridwrap--short {
  max-height: 40vh;
}

.amd__row {
  height: 44px;
}

.amd__cell {
  overflow: hidden;
  color: var(--text);
  font-size: 13px;
  line-height: var(--lh-13);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.amd__num {
  text-align: right;
}

.amd__cell--actions {
  text-align: right;
  white-space: nowrap;
}

/* 名字是按钮：清掉按钮外观，让它读起来像一行标题而不是一个控件 —— 但它在 Tab 顺序里，
   键盘用户到得了（`.amd__name` 的 hover 只变色，不移位）。 */
.amd__name {
  display: flex;
  flex-direction: column;
  max-width: 100%;
  padding: 0;
  background: transparent;
  border: 0;
  text-align: left;
  cursor: pointer;
}

.amd__nameMain {
  overflow: hidden;
  color: var(--ink);
  font-size: 13px;
  line-height: var(--lh-13);
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (hover: hover) and (pointer: fine) {
  .amd__name:hover .amd__nameMain {
    color: var(--text);
  }
}

.amd__nameSlug,
.amd__nameStatic {
  overflow: hidden;
  color: var(--muted);
  text-overflow: ellipsis;
}

.amd__dim {
  color: var(--muted);
}

.amd__tag {
  display: inline-block;
  padding: 2px 8px;
  background: var(--fill);
  border-radius: var(--radius-sm);
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
}

/* 「已上架 / 已停用」是页面里**唯一**需要一眼分辨的状态，所以只有这两处用状态三件套
   （底色 + 文字色；记号色那一档不写文字）。 */
.amd__tag--on {
  background: var(--ok-wash);
  color: var(--ok-ink);
}

.amd__tag--off {
  background: var(--danger-wash);
  color: var(--danger-ink);
}

.amd__caps {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 4px;
}

.amd__usage {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.amd__usageMain {
  color: var(--ink);
}

.amd__audit {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}

.amd__auditSkeleton {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 16px;
}

.amd__auditEmpty {
  margin: 0;
  padding: 20px 16px;
  color: var(--muted);
}

.amd__auditRows {
  margin: 0;
  padding: 0;
  list-style: none;
}

.amd__auditRow {
  padding: 8px 16px;
  border-bottom: 1px solid var(--line);
}

.amd__auditRow:last-child {
  border-bottom: 0;
}

.amd__auditLine {
  display: grid;
  grid-template-columns: 88px 140px minmax(0, 1fr) 56px minmax(0, 1.2fr) auto;
  gap: 12px;
  align-items: baseline;
}

.amd__auditTime {
  color: var(--muted);
}

.amd__auditWho {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.amd__auditWhat {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.amd__auditTarget {
  color: var(--muted);
}

.amd__auditDetail {
  overflow: hidden;
  color: var(--muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.amd__auditDiffBtn {
  justify-self: end;
}

/* 文本按钮（config 行的「查看改法」、审计行的「查看改动」）：看起来像一格文字，
   行为是一个按钮 —— hover 只变色不移位。 */
.amd__textbtn {
  padding: 0;
  background: transparent;
  border: 0;
  color: var(--accent-ink);
  font-size: 13px;
  line-height: var(--lh-13);
  white-space: nowrap;
  cursor: pointer;
}

@media (hover: hover) and (pointer: fine) {
  .amd__textbtn:hover {
    color: var(--accent-press);
  }
}

/* 页头健康灯：8px 圆点 + 一句状态。它是「常在」的那一眼，细节在 title 与
   gatewayDown 警告条上。 */
.amd__health {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.amd__healthdot {
  flex: 0 0 auto;
  width: 8px;
  height: 8px;
  border-radius: var(--radius-pill);
}

.amd__healthtext {
  overflow: hidden;
  max-width: 260px;
  color: var(--muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 网关健康灯的色调。 */
.amd__dot--ok {
  background: var(--ok);
}

.amd__dot--danger {
  background: var(--danger);
}

.amd__reason {
  overflow: hidden;
  max-width: 100%;
  text-overflow: ellipsis;
}

.amd__usageRow {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

/* 行内 sparkline 的槽位：固定 64×20，多出来的横向空间让给数字。 */
.amd__spark {
  flex: 0 0 auto;
  width: 64px;
}

.amd__rate--muted {
  color: var(--muted);
}

.amd__rate--warn {
  color: var(--warn-ink);
}

.amd__rate--danger {
  color: var(--danger-ink);
}

.amd__ok {
  color: var(--ok-ink);
}

.amd__fail {
  color: var(--danger-ink);
}

/* 窄屏：审计行收成三列，把「改的是什么」那一格让给正文。操作人和时间都还在
   （它们是这一行「谁改了什么」的一半），只让说明那一格换行到下面。 */
@media (max-width: 900px) {
  .amd__auditLine {
    grid-template-columns: 64px 92px minmax(0, 1fr) 48px auto;
    gap: 8px;
  }

  .amd__auditDetail {
    grid-column: 3 / -1;
  }
}

/* 手机（≤700）：网格换成折行的 flex。五列到了 390px 上，「改的是什么」那一格只剩
   六十来像素 —— 而它是这一行的正文。让它独占一行，时间 / 谁 / 结果挤在上面那一行，
   「谁在什么时候改了什么」还是按那个顺序读。 */
@media (max-width: 700px) {
  .amd__auditLine {
    display: flex;
    flex-wrap: wrap;
    gap: 2px 8px;
    align-items: baseline;
  }

  .amd__auditWhat {
    flex: 1 1 100%;
    white-space: normal;
  }

  .amd__auditDetail {
    min-width: 0;
  }

  .amd__auditDiffBtn {
    margin-left: auto;
  }
}
</style>
