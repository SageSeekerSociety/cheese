<script setup lang="ts">
import type { ModelFormPayload } from '@/components/admin/AdminModelFormDialog.vue'
import type { ModelDetailPayload } from '@/composables/useAdminModelDetail'
import type { ResolvedUserRef } from '@/composables/useUserRefResolver'
import type { ModelTier } from '@/lib/adminCredits'
import type { AuditItem, ModelRow, ModelsListing, ProjectRow, ProjectsPayload } from '@/lib/adminModels'

import { useI18n } from 'vue-i18n'

import AdminBudgetDialog from '@/components/admin/AdminBudgetDialog.vue'
import AdminKpiCard from '@/components/admin/AdminKpiCard.vue'
import AdminModelDetailDrawerView from '@/components/admin/AdminModelDetailDrawerView.vue'
import AdminModelFormDialog from '@/components/admin/AdminModelFormDialog.vue'
import AdminPage from '@/components/admin/AdminPage.vue'
import AdminModelsAuditView from '@/components/admin/models/AdminModelsAuditView.vue'
import AdminModelsBudgets from '@/components/admin/models/AdminModelsBudgets.vue'
import AdminModelsConfirmDialog from '@/components/admin/models/AdminModelsConfirmDialog.vue'
import AdminModelsFlash from '@/components/admin/models/AdminModelsFlash.vue'
import AdminModelsHeader from '@/components/admin/models/AdminModelsHeader.vue'
import AdminModelsTable from '@/components/admin/models/AdminModelsTable.vue'
import BaseButton from '@/components/base/BaseButton.vue'

// 管理后台「模型管理」**画的那一半**：三段（模型 / 额度 / 最近操作）、页头那六个数、四个
// 对话框、详情抽屉。
//
// 取数在容器的 `useAdminModels` 里（三段各自的读、窗口、写操作的四处状态、详情抽屉那份
// 数据），各段的画法在 `components/admin/models/*.vue`；这一件只吃 props、只往上发事件。
// 详细的那段道理（哪一段失败在哪儿说、为什么 config 来源是只读、窗口为什么是页面级的）
// 写在容器顶上。
//
// 这一件里有三处**视图**（`AdminModelsAuditView` / `AdminModelDetailDrawerView` /
// 其余各段）：它们的容器（`AdminModelsAudit.vue`、`AdminModelDetailDrawer.vue`）留着的是
// 各自那份取数与人名，页面这条路直接用视图，数据由上面那份 props 供给。
defineOptions({ name: 'AdminModelsPageView' })

const props = defineProps<{
  subLine: string
  health: { ok: boolean; text: string; title: string } | null
  /** 统计窗口，三段共用。 */
  days: number
  windows: number[]
  loading: boolean
  /** 写操作（删除 / 停用 / 改额度）失败的原话。页顶那条横条只说这一种失败。 */
  writeError: string | null
  notice: string | null
  kpis: { key: string; label: string; value: string }[]
  /** 模型段。`null` = 还没到货（或读失败，见 `modelsState`）。 */
  models: ModelsListing | null
  modelsState: 'rows' | 'error'
  loadError: string | null
  gatewayDetail: string
  /** 网关不可达 / 没配管理密钥：「新增模型」那颗按钮跟着灰掉。 */
  gatewayDown: boolean
  projects: ProjectsPayload | null
  projectsLoading: boolean
  projectsState: 'rows' | 'error'
  projectsError: string | null
  projectTotalsText: string
  audit: AuditItem[]
  auditLoading: boolean
  auditError: string | null
  auditExpanded: Set<number>
  formOpen: boolean
  formMode: 'add' | 'edit'
  formSeed: ModelRow | null
  formSaving: boolean
  formError: string | null
  budgetOpen: boolean
  budgetProject: ProjectRow | null
  budgetSaving: boolean
  budgetError: string | null
  deleteTarget: ModelRow | null
  deleting: boolean
  blockTarget: ModelRow | null
  blocking: boolean
  drawerOpen: boolean
  drawerName: string | null
  drawerDetail: ModelDetailPayload | null
  drawerDetailLoading: boolean
  drawerDetailError: string | null
  resolveUser: (handle: string | null | undefined) => ResolvedUserRef
}>()

const emit = defineEmits<{
  changeWindow: [value: number]
  reload: []
  clearFlash: []
  openAdd: []
  openDetail: [row: ModelRow]
  edit: [row: ModelRow]
  block: [row: ModelRow]
  delete: [row: ModelRow]
  tier: [row: ModelRow, tier: ModelTier]
  reloadProjects: []
  setBudget: [row: ProjectRow]
  reloadAudit: []
  toggleAudit: [index: number]
  submitForm: [payload: ModelFormPayload]
  'update:formOpen': [open: boolean]
  closeDelete: []
  confirmDelete: []
  closeBlock: []
  confirmBlock: []
  submitBudget: [maxBudgetUsd: number | null]
  'update:budgetOpen': [open: boolean]
  closeDetail: []
  reloadDetail: []
  navigate: [target: ResolvedUserRef['to']]
}>()

const { t } = useI18n()
</script>

<template>
  <div class="amd">
    <AdminPage :title="t('navigation.admin.models')" :sub="props.subLine">
      <template #tools>
        <AdminModelsHeader
          :health="props.health"
          :days="props.days"
          :windows="props.windows"
          :loading="props.loading"
          @change-window="emit('changeWindow', $event)"
          @refresh="emit('reload')"
        />
      </template>

      <div class="amd__body admin-page__body">
        <!-- 写失败 / 提示。**读失败不在这里说** —— 那一条画在各自那一段的位置上
             （表的列头下面、审计那张卡里），同一次失败说两遍，人会以为是两次。 -->
        <AdminModelsFlash :error="props.writeError" :notice="props.notice" @dismiss="emit('clearFlash')" />

        <div class="amd__kpis">
          <AdminKpiCard
            v-for="kpi in props.kpis"
            :key="kpi.key"
            :label="kpi.label"
            :value="kpi.value"
            :loading="props.loading"
          />
        </div>

        <!-- 模型段。页面上唯一的主操作（新增模型）在这一段，所以琥珀只出现在这里一处。
             这一段不另写小标题：页头已经叫「模型」。 -->
        <section class="amd__section">
          <div class="amd__sectiontools">
            <div class="amd__spacer" />
            <BaseButton
              kind="primary"
              size="sm"
              prepend-icon="mdi-plus"
              :disabled="props.gatewayDown"
              @click="emit('openAdd')"
            >
              {{ t('models.page.add') }}
            </BaseButton>
          </div>

          <AdminModelsTable
            :models="props.models"
            :loading="props.loading"
            :state="props.modelsState"
            :error="props.loadError"
            :gateway-detail="props.gatewayDetail"
            @retry="emit('reload')"
            @detail="emit('openDetail', $event)"
            @edit="emit('edit', $event)"
            @block="emit('block', $event)"
            @delete="emit('delete', $event)"
            @tier="(row, tier) => emit('tier', row, tier)"
          />
        </section>

        <!-- 额度段。一眼要看出「剩余 / 已用 / 刹车值 / 是不是不限量」四件事。 -->
        <section class="amd__section">
          <div class="amd__sectiontools">
            <h2 class="amd__sectionlabel t-title">{{ t('models.page.section.budgets') }}</h2>
            <span class="amd__count amd__summary t-meta-read">{{ props.projectTotalsText }}</span>
          </div>

          <AdminModelsBudgets
            :projects="props.projects"
            :loading="props.projectsLoading"
            :state="props.projectsState"
            :error="props.projectsError"
            @retry="emit('reloadProjects')"
            @set-budget="emit('setBudget', $event)"
          />
        </section>

        <!-- 最近操作段：写操作是危险动作，改完要留痕、要能回看。 -->
        <section class="amd__section">
          <div class="amd__sectiontools">
            <h2 class="amd__sectionlabel t-title">{{ t('models.page.section.audit') }}</h2>
          </div>

          <AdminModelsAuditView
            :items="props.audit"
            :loading="props.auditLoading"
            :error="props.auditError"
            :expanded="props.auditExpanded"
            :resolve-user="props.resolveUser"
            @retry="emit('reloadAudit')"
            @toggle="emit('toggleAudit', $event)"
            @navigate="emit('navigate', $event)"
          />
        </section>
      </div>
    </AdminPage>

    <AdminModelDetailDrawerView
      :open="props.drawerOpen"
      :name="props.drawerName"
      :detail="props.drawerDetail"
      :loading="props.drawerDetailLoading"
      :error="props.drawerDetailError"
      @close="emit('closeDetail')"
      @retry="emit('reloadDetail')"
    />

    <AdminModelFormDialog
      :model-value="props.formOpen"
      :mode="props.formMode"
      :seed="props.formSeed"
      :saving="props.formSaving"
      :error="props.formError"
      @update:model-value="emit('update:formOpen', $event)"
      @submit="emit('submitForm', $event)"
    />

    <AdminBudgetDialog
      :model-value="props.budgetOpen"
      :project="props.budgetProject"
      :saving="props.budgetSaving"
      :error="props.budgetError"
      @update:model-value="emit('update:budgetOpen', $event)"
      @submit="emit('submitBudget', $event)"
    />

    <!-- 删除确认。说清后果（模型从网关移除、项目再也选不到它）。 -->
    <AdminModelsConfirmDialog
      :model-value="!!props.deleteTarget"
      :title="t('models.confirm.delete.title')"
      :body="t('models.confirm.delete.body', { name: props.deleteTarget?.name ?? '' })"
      :confirm-label="t('models.confirm.delete.confirm')"
      :busy="props.deleting"
      danger
      @update:model-value="emit('closeDelete')"
      @confirm="emit('confirmDelete')"
    />

    <!-- 停用 / 启用确认。停用会把模型从选择器里摘掉，所以要说出来。 -->
    <AdminModelsConfirmDialog
      :model-value="!!props.blockTarget"
      :title="t(props.blockTarget?.blocked ? 'models.confirm.unblock.title' : 'models.confirm.block.title')"
      :body="
        t(props.blockTarget?.blocked ? 'models.confirm.unblock.body' : 'models.confirm.block.body', {
          name: props.blockTarget?.name ?? '',
        })
      "
      :confirm-label="t(props.blockTarget?.blocked ? 'models.confirm.unblock.confirm' : 'models.confirm.block.confirm')"
      :busy="props.blocking"
      :danger="!props.blockTarget?.blocked"
      @update:model-value="emit('closeBlock')"
      @confirm="emit('confirmBlock')"
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
