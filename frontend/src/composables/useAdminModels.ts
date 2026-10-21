// 模型管理页**取数**的那一半：三段各自的读、各自的失败，模型与线路的写操作状态，窗口，
// 以及页头和 KPI 要的那几个判据。这一份是拼起来的那一处，也是这一页对外的样子。
//
// 分家的理由和 `useAdminStats` 是同一个形状：这一页原先一份 `<script setup>` 一千
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
//
// 这一份自己只留三样：三段共用的窗口与横幅（写失败 / 提示，同一时刻只画一条）、
// 详情抽屉那三个 ref、以及把下面几块拼成宿主认得的那一个对象。其余各归各的：
//
//   三段的读与它们共用的窗口 → `adminModels/reads.ts`
//   页头与顶上的判据、读数   → `adminModels/derive.ts`
//   模型那一段的写           → `adminModels/writes.ts`
//   线路那一段的写           → `adminModels/sources.ts`
//   额度那一段的写           → `adminModels/budget.ts`
//   几块共用的常量与取原话   → `adminModels/shared.ts`
import type { ModelRow } from '@/lib/adminModels'

import { onMounted, ref } from 'vue'

import { useAdminModelsBudget } from '@/composables/adminModels/budget'
import { useAdminModelsCaution } from '@/composables/adminModels/caution'
import { useAdminModelsDerive } from '@/composables/adminModels/derive'
import { useAdminModelsReads } from '@/composables/adminModels/reads'
import { WINDOWS } from '@/composables/adminModels/shared'
import { useAdminModelsSources } from '@/composables/adminModels/sources'
import { useAdminModelsWrites } from '@/composables/adminModels/writes'

export function useAdminModels() {
  // 页顶那条横条：写失败与提示互斥，同一时刻只画一条。写的那几块往里放，页面读它，
  // 那个叉两支一起清。
  const writeError = ref<string | null>(null)
  const notice = ref<string | null>(null)

  /** 详情抽屉：开着哪一个模型（按名字），以及写完一条线路之后重拉的那只计数器。 */
  const drawerOpen = ref(false)
  const drawerName = ref<string | null>(null)
  const drawerReload = ref(0)

  const {
    days,
    models,
    projects,
    audit,
    loading,
    projectsLoading,
    auditLoading,
    loadError,
    projectsError,
    auditError,
    auditExpanded,
    load,
    loadProjects,
    loadAudit,
    changeWindow,
    toggleAuditDiff,
  } = useAdminModelsReads()

  const { gatewayDown, gatewayDetail, modelsState, projectsState, health, kpis, num, subLine, projectTotalsText } =
    useAdminModelsDerive({ models, projects, loadError, projectsError })

  const {
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
    setOverseas,
  } = useAdminModelsWrites({ writeError, notice, load })

  const {
    sourceFormOpen,
    sourceFormMode,
    sourceModel,
    sourceSeed,
    sourceSiblings,
    sourceSaving,
    sourceError,
    sourceBusy,
    openAddSource,
    openEditSource,
    onSourceSubmit,
    moveSource,
    sourceDeleteTarget,
    askSourceDelete,
    closeSourceDelete,
    confirmSourceDelete,
    sourceBlockTarget,
    askSourceBlock,
    closeSourceBlock,
    confirmSourceBlock,
  } = useAdminModelsSources({ writeError, notice, load, drawerReload })

  // 动「别处在用」的模型之前先说清楚（`adminModels/caution.ts`）：下面这些入口都过它。
  const { cautionTarget, cautionBody, heed, confirmCaution, closeCaution } = useAdminModelsCaution(models)
  const byRow = (row: ModelRow) => row.name
  const byName = (name: string) => name

  const { budgetOpen, budgetProject, budgetSaving, budgetError, openBudget, onBudgetSubmit } = useAdminModelsBudget({
    loadProjects,
    notice,
  })

  function openDetail(row: ModelRow) {
    drawerName.value = row.name
    drawerOpen.value = true
  }

  /** 页顶那条横条上的叉：横条同一时刻只画一条（写失败与提示互斥），所以两支一起清，
   *  效果和「清掉正在显示的那一条」逐字相同，页面上不用再分辨是哪一条。 */
  function clearFlash() {
    writeError.value = null
    notice.value = null
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
    openEdit: heed(byRow, openEdit),
    onFormSubmit,
    deleteTarget,
    deleting,
    askDelete: heed(byRow, askDelete),
    closeDelete,
    confirmDelete,
    blockTarget,
    blocking,
    askBlock: heed(byRow, askBlock),
    closeBlock,
    confirmBlock,
    setTier: heed(byRow, setTier),
    setOverseas: heed(byRow, setOverseas),
    // 详情抽屉
    drawerOpen,
    drawerName,
    drawerReload,
    openDetail,
    // 线路
    sourceFormOpen,
    sourceFormMode,
    sourceModel,
    sourceSeed,
    sourceSiblings,
    sourceSaving,
    sourceError,
    sourceBusy,
    openAddSource: heed(byName, openAddSource),
    openEditSource: heed(byName, openEditSource),
    onSourceSubmit,
    moveSource: heed(byName, moveSource),
    sourceDeleteTarget,
    askSourceDelete: heed(byName, askSourceDelete),
    closeSourceDelete,
    confirmSourceDelete,
    sourceBlockTarget,
    askSourceBlock: heed(byName, askSourceBlock),
    closeSourceBlock,
    confirmSourceBlock,
    // 额度段
    budgetOpen,
    budgetProject,
    budgetSaving,
    budgetError,
    openBudget,
    onBudgetSubmit,
    // 动「别处在用」的模型之前的那一问
    cautionTarget,
    cautionBody,
    confirmCaution,
    closeCaution,
    // 审计段
    auditExpanded,
    toggleAuditDiff,
  }
}
