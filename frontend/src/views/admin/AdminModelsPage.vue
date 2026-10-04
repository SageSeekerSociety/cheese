<script setup lang="ts">
import { useI18n } from 'vue-i18n'

import { useAdminModels } from '@/composables/useAdminModels'

import AdminBudgetDialog from '@/components/admin/AdminBudgetDialog.vue'
import AdminKpiCard from '@/components/admin/AdminKpiCard.vue'
import AdminModelDetailDrawer from '@/components/admin/AdminModelDetailDrawer.vue'
import AdminModelFormDialog from '@/components/admin/AdminModelFormDialog.vue'
import AdminPage from '@/components/admin/AdminPage.vue'
import AdminModelsAudit from '@/components/admin/models/AdminModelsAudit.vue'
import AdminModelsBudgets from '@/components/admin/models/AdminModelsBudgets.vue'
import AdminModelsConfirmDialog from '@/components/admin/models/AdminModelsConfirmDialog.vue'
import AdminModelsFlash from '@/components/admin/models/AdminModelsFlash.vue'
import AdminModelsHeader from '@/components/admin/models/AdminModelsHeader.vue'
import AdminModelsTable from '@/components/admin/models/AdminModelsTable.vue'
import BaseButton from '@/components/base/BaseButton.vue'

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

const { t } = useI18n()

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
  <div class="amd">
    <AdminPage :title="t('navigation.admin.models')" :sub="subLine">
      <template #tools>
        <AdminModelsHeader
          :health="health"
          :days="days"
          :windows="windows"
          :loading="loading"
          @change-window="changeWindow"
          @refresh="load"
        />
      </template>

      <div class="amd__body admin-page__body">
        <!-- 写失败 / 提示。**读失败不在这里说** —— 那一条画在各自那一段的位置上
             （表的列头下面、审计那张卡里），同一次失败说两遍，人会以为是两次。 -->
        <AdminModelsFlash :error="writeError" :notice="notice" @dismiss="clearFlash" />

        <div class="amd__kpis">
          <AdminKpiCard v-for="kpi in kpis" :key="kpi.key" :label="kpi.label" :value="kpi.value" :loading="loading" />
        </div>

        <!-- 模型段。页面上唯一的主操作（新增模型）在这一段，所以琥珀只出现在这里一处。
             这一段不另写小标题：页头已经叫「模型」。 -->
        <section class="amd__section">
          <div class="amd__sectiontools">
            <div class="amd__spacer" />
            <BaseButton kind="primary" size="sm" prepend-icon="mdi-plus" :disabled="gatewayDown" @click="openAdd">
              {{ t('models.page.add') }}
            </BaseButton>
          </div>

          <AdminModelsTable
            :models="models"
            :loading="loading"
            :state="modelsState"
            :error="loadError"
            :gateway-detail="gatewayDetail"
            @retry="load"
            @detail="openDetail"
            @edit="openEdit"
            @block="askBlock"
            @delete="askDelete"
            @tier="setTier"
          />
        </section>

        <!-- 额度段。一眼要看出「剩余 / 已用 / 刹车值 / 是不是不限量」四件事。 -->
        <section class="amd__section">
          <div class="amd__sectiontools">
            <h2 class="amd__sectionlabel t-title">{{ t('models.page.section.budgets') }}</h2>
            <span class="amd__count amd__summary t-meta-read">{{ projectTotalsText }}</span>
          </div>

          <AdminModelsBudgets
            :projects="projects"
            :loading="projectsLoading"
            :state="projectsState"
            :error="projectsError"
            @retry="loadProjects"
            @set-budget="openBudget"
          />
        </section>

        <!-- 最近操作段：写操作是危险动作，改完要留痕、要能回看。 -->
        <section class="amd__section">
          <div class="amd__sectiontools">
            <h2 class="amd__sectionlabel t-title">{{ t('models.page.section.audit') }}</h2>
          </div>

          <AdminModelsAudit
            :items="audit"
            :loading="auditLoading"
            :error="auditError"
            :expanded="auditExpanded"
            @retry="loadAudit"
            @toggle="toggleAuditDiff"
          />
        </section>
      </div>
    </AdminPage>

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
    <AdminModelsConfirmDialog
      :model-value="!!deleteTarget"
      :title="t('models.confirm.delete.title')"
      :body="t('models.confirm.delete.body', { name: deleteTarget?.name ?? '' })"
      :confirm-label="t('models.confirm.delete.confirm')"
      :busy="deleting"
      danger
      @update:model-value="closeDelete"
      @confirm="confirmDelete"
    />

    <!-- 停用 / 启用确认。停用会把模型从选择器里摘掉，所以要说出来。 -->
    <AdminModelsConfirmDialog
      :model-value="!!blockTarget"
      :title="t(blockTarget?.blocked ? 'models.confirm.unblock.title' : 'models.confirm.block.title')"
      :body="
        t(blockTarget?.blocked ? 'models.confirm.unblock.body' : 'models.confirm.block.body', {
          name: blockTarget?.name ?? '',
        })
      "
      :confirm-label="t(blockTarget?.blocked ? 'models.confirm.unblock.confirm' : 'models.confirm.block.confirm')"
      :busy="blocking"
      :danger="!blockTarget?.blocked"
      @update:model-value="closeBlock"
      @confirm="confirmBlock"
    />
  </div>
</template>

<style scoped>
/* 高度给满：滚动由 `AppPage` 的正文领。 */
.amd {
  height: 100%;
  min-height: 0;
}

.amd__body {
  display: flex;
  flex-direction: column;
}

.amd__kpis {
  display: grid;
  /* 六张卡，每档都**整除**：6 / 3 / 2。auto-fit 那类写法会在某些宽度上留下最后
     一张孤零零独占一行，而这几张是并列读的指标，不是排队的东西。 */
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 16px;
}

/* 后台页的断点挂在内容列上（§3.5），不用视口媒体查询：侧栏收起省出的宽度，视口
   查询看不见。容器是 `.app-page__column--admin`（名字 `admin`）。
   6 轨 KPI 正好要 1440（`--page-w-admin`：6×216 + 内距），容器窄于 1320 就掉到 3 轨。 */
@container admin (max-width: 1319.98px) {
  .amd__kpis {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

/* 窄容器 KPI 退成两列（同看板）：一排三张在手机上每张不到 150px，字会被压破。 */
@container admin (max-width: 719.98px) {
  .amd__kpis {
    grid-template-columns: repeat(2, minmax(0, 1fr));
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
</style>
