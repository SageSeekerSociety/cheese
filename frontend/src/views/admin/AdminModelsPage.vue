<script setup lang="ts">
import { useAdminModels } from '@/composables/useAdminModels'
import { useUserRefResolver } from '@/composables/useUserRefResolver'

import AdminModelsPageView from './AdminModelsPageView.vue'

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
//
// 这一件现在只剩**接线**：取数在 `composables/useAdminModels.ts`，各段的画法在
// `components/admin/models/*.vue`，两条对应关系（哪一段点哪一下调哪个动作）看下面那个
// 模板就够了。三段各自的失败留在各自的段里，这一点没变。
//
// 再往下拆了一层**容器 + 视图**：画面（三段、页头那六个数、四个对话框、详情抽屉）搬到
// `AdminModelsPageView.vue`，只吃 props、只发事件；这一半留着的就是上面那些规矩和这里
// 下面这一串接线 —— 包括人名那一颗的 `resolveUser`（审计行里的「谁改的」）。
const { resolve: resolveUser, navigate } = useUserRefResolver()

const {
  days,
  windows,
  subLine,
  health,
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
  gatewayDown,
  gatewayDetail,
  modelsState,
  projectsState,
  kpis,
  projectTotalsText,
  writeError,
  notice,
  clearFlash,
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
  setTier,
  closeDelete,
  confirmDelete,
  blockTarget,
  blocking,
  askBlock,
  closeBlock,
  confirmBlock,
  drawerOpen,
  drawerName,
  drawerDetail,
  drawerDetailLoading,
  drawerDetailError,
  loadDetail,
  closeDetail,
  openDetail,
  budgetOpen,
  budgetProject,
  budgetSaving,
  budgetError,
  openBudget,
  onBudgetSubmit,
  auditExpanded,
  toggleAuditDiff,
} = useAdminModels()
</script>

<template>
  <AdminModelsPageView
    :sub-line="subLine"
    :health="health"
    :days="days"
    :windows="windows"
    :loading="loading"
    :write-error="writeError"
    :notice="notice"
    :kpis="kpis"
    :models="models"
    :models-state="modelsState"
    :load-error="loadError"
    :gateway-detail="gatewayDetail"
    :gateway-down="gatewayDown"
    :projects="projects"
    :projects-loading="projectsLoading"
    :projects-state="projectsState"
    :projects-error="projectsError"
    :project-totals-text="projectTotalsText"
    :audit="audit"
    :audit-loading="auditLoading"
    :audit-error="auditError"
    :audit-expanded="auditExpanded"
    :form-open="formOpen"
    :form-mode="formMode"
    :form-seed="formSeed"
    :form-saving="formSaving"
    :form-error="formError"
    :budget-open="budgetOpen"
    :budget-project="budgetProject"
    :budget-saving="budgetSaving"
    :budget-error="budgetError"
    :delete-target="deleteTarget"
    :deleting="deleting"
    :block-target="blockTarget"
    :blocking="blocking"
    :drawer-open="drawerOpen"
    :drawer-name="drawerName"
    :drawer-detail="drawerDetail"
    :drawer-detail-loading="drawerDetailLoading"
    :drawer-detail-error="drawerDetailError"
    :resolve-user="resolveUser"
    @change-window="changeWindow"
    @reload="load"
    @clear-flash="clearFlash"
    @open-add="openAdd"
    @open-detail="openDetail"
    @edit="openEdit"
    @block="askBlock"
    @delete="askDelete"
    @tier="setTier"
    @reload-projects="loadProjects"
    @set-budget="openBudget"
    @reload-audit="loadAudit"
    @toggle-audit="toggleAuditDiff"
    @submit-form="onFormSubmit"
    @update:form-open="formOpen = $event"
    @close-delete="closeDelete"
    @confirm-delete="confirmDelete"
    @close-block="closeBlock"
    @confirm-block="confirmBlock"
    @submit-budget="onBudgetSubmit"
    @update:budget-open="budgetOpen = $event"
    @close-detail="closeDetail"
    @reload-detail="loadDetail"
    @navigate="navigate"
  />
</template>
