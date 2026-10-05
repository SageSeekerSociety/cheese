<script setup lang="ts">
// 发题页的**画**那一半：页头、两张画面（手写一道 / 从 PDF 生成），以及手写那道底下
// 那几件共享机器（表单、材料、从 PDF 生成）。取数、装配、发题与跳转都在容器
// `PublishTask.vue` 里 —— 这一件只吃 props、只往上发事件。
//
// 「选完模板再挂表单」那条装配顺序写在容器的文件头；这里只认 `ready` 那一枚开关。
import type { PublishCheck } from '@/lib/taskPublishChecks'
import type {
  ConfirmTaskFromPdfRequestData,
  ConfirmTaskFromPdfResponseData,
  CreateTaskFromPdfRequestData,
  PdfTaskDraftData,
  PreviewTaskFromPdfResponseData,
} from '@/network/api/tasks/types'
import type {
  DomainGroup,
  SpaceCategory,
  SpaceMaterial,
  SpaceMaterialsState,
  SpaceTeaching,
  TaskFormSubmitData,
  Topic,
} from '@/types'

import { computed, defineAsyncComponent } from 'vue'
import { useI18n } from 'vue-i18n'

import PdfGenerate from './PdfGenerate.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import TeachingFields from '@/components/common/TeachingFields.vue'
import PanelCard from '@/components/spaces/PanelCard.vue'
import { spaceLibraryPath } from '@/lib/spaceRouteNames'

const TaskForm = defineAsyncComponent(() => import('@/components/tasks/TaskForm.vue'))
const TaskAttachmentPicker = defineAsyncComponent(() => import('@/components/tasks/TaskAttachmentPicker.vue'))

const { t } = useI18n()

defineProps<{
  /** 「PDF 快速发布」那张卡：解析中 / 批量确认中。 */
  quickLoading: boolean
  quickConfirming: boolean
  /** `?templateId=` 带过来的（或 PDF 草稿第一条）表单初值。 */
  initialTaskData: Record<string, unknown>
  classificationTopics: Topic[]
  /** 活跃（未归档）的分类，与老页同一口径：下拉里只有能选的。 */
  activeCategories: SpaceCategory[]
  preselectedCategoryId?: number
  domainGroups: DomainGroup[]
  /** 材料卡：正在上传的那面旗（用它挡提交）。 */
  formChecks: PublishCheck[] | null
  /** 空间装完没有：没装完不挂表单（见容器文件头「装完再挂」）。 */
  ready: boolean
  /** 这道题发出去之后会经过哪几站，那句话跟着身份变。 */
  isManager: boolean
  spaceId: number
  /** 用哪份模板读 PDF（`-1` 是空白）。 */
  pdfTemplateIndex: number
  /** 这道题自己的「给 AI 队友的指导」覆盖；六格全空就是 `undefined`。 */
  teachingPayload?: SpaceTeaching
  teachingMaterials: SpaceMaterial[]
  teachingMaterialsState: SpaceMaterialsState
  /** 「从 PDF 生成」那条路：这块板的分类（含归档），草稿的 `categoryId` 拿它换名字。 */
  categories: SpaceCategory[]
  /** 请求里带的那个 id（容器那边的 `spaceStore.currentSpaceId`）。 */
  currentSpaceId: number | null
  /** PDF 快速发布解析出来的草稿，以及这次烧掉多少 token。 */
  quickDrafts: PdfTaskDraftData[]
  quickTokens: number | null
  /** 材料那两道接口（`POST /attachments`、`GET /attachments/limits`）。 */
  loadLimit: () => Promise<number | null>
  upload: (file: File) => Promise<{ id: number }>
  /** 「从 PDF 生成」那条路的两个接口（`preview` / `confirm`）。 */
  previewFromPdf: (payload: CreateTaskFromPdfRequestData) => Promise<{ data: PreviewTaskFromPdfResponseData }>
  confirmFromPdf: (payload: ConfirmTaskFromPdfRequestData) => Promise<{ data: ConfirmTaskFromPdfResponseData }>
}>()

/** 两条发题路：手写一道，或从一份 PDF 里批量生成。 */
const mode = defineModel<'write' | 'pdf'>('mode', { required: true })
/** 「PDF 快速发布」那张卡上传的那份 PDF（单文件/多文件两种形状都出现过）。 */
const quickFile = defineModel<File | File[] | null>('quickFile', { required: true })
/** 「给 AI 队友的指导」那一节摊开没有（默认收起）。 */
const teachingOpen = defineModel<boolean>('teachingOpen', { required: true })
/** 这道题自己的指导覆盖。 */
const teachingOverride = defineModel<SpaceTeaching>('teachingOverride', { required: true })
/** 材料卡报上来的：随题一起发出的材料 id，以及此刻是否正在上传。 */
const attachmentIds = defineModel<number[]>('attachmentIds', { required: true })
const attachmentUploading = defineModel<boolean>('attachmentUploading', { required: true })

const emit = defineEmits<{
  /** 「预览」那颗按钮：真发一次 `POST /tasks/publish/from-pdf/preview`。 */
  previewQuick: []
  /** 「清空预览」：把表单从「只填参数」还回「手写一道」。 */
  clearQuick: []
  /** 真表单交上来的提交（`POST /tasks` 或批量 `confirm`）。 */
  submitTask: [data: TaskFormSubmitData]
  /** 右栏清单那颗「提交审核」：走的是真表单自己的提交。 */
  submitFromChecklist: []
}>()

/** `v-file-input` 单文件/多文件两种返回形状都出现过，统一成一份。 */
const selectedQuickPdf = computed<File | null>(() => {
  if (Array.isArray(quickFile.value)) return quickFile.value[0] ?? null
  return quickFile.value
})

/** 草稿内容预览。原型那一版能就地改题干，这一条**不能** —— 它落到的还是下面那张
 *  共享表单（发布参数在表单里填），能改的是「从 PDF 生成」那条路。 */
function previewDescription(value: unknown): string {
  if (!value) return '—'
  const text = String(value).replace(/\s+/g, ' ').trim()
  return text.length > 180 ? `${text.slice(0, 180)}…` : text || '—'
}

/** 真表单交上来的提交，往容器上递。 */
function onSubmit(data: TaskFormSubmitData) {
  emit('submitTask', data)
}
</script>

<template>
  <PageHeader show-on-mobile>
    <template #actions>
      <v-btn-toggle v-model="mode" density="compact" variant="outlined" divided mandatory class="pub__mode">
        <!-- eslint-disable-next-line vue/no-restricted-syntax -- a segment of v-btn-toggle, not one of the BaseButton roles -->
        <v-btn value="write" size="small" prepend-icon="mdi-pencil-outline">{{
          t('spaces.detail.publishTask.mode.write')
        }}</v-btn>
        <!-- eslint-disable-next-line vue/no-restricted-syntax -- a segment of v-btn-toggle, not one of the BaseButton roles -->
        <v-btn value="pdf" size="small" prepend-icon="mdi-file-pdf-box">{{
          t('spaces.detail.publishTask.mode.pdf')
        }}</v-btn>
      </v-btn-toggle>
    </template>
  </PageHeader>
  <div class="pub">
    <!-- ============ 手写一道 ============ -->
    <div v-if="mode === 'write'" class="pub__grid">
      <div class="pub__main">
        <!-- 老页本来那张「PDF 快速发布」：先解析出草稿，再在下面那张表单里填参数、
             一次批量发出去。原型里没有这张卡（原型把 PDF 整条路放在另一个态里），
             所以形状照原型画：一张面板、标题加一句说明、按钮排在右下。 -->
        <PanelCard
          :title="t('spaces.detail.publishTask.quick.title')"
          :subtitle="t('spaces.detail.publishTask.quick.subtitle')"
        >
          <v-file-input
            v-model="quickFile"
            accept=".pdf,application/pdf"
            :label="t('spaces.detail.publishTask.quick.uploadLabel')"
            variant="outlined"
            density="comfortable"
            clearable
            prepend-icon=""
            hide-details="auto"
            :disabled="quickLoading || quickConfirming"
          >
            <template #prepend>
              <v-icon color="primary" class="mr-2">mdi-upload</v-icon>
            </template>
          </v-file-input>

          <p class="pdf__hint">
            {{ t('spaces.detail.publishTask.quick.hint') }}
          </p>

          <div class="pdf__actions">
            <span class="pdf__actions-note">{{ t('spaces.detail.publishTask.quick.readOnlyNote') }}</span>
            <v-spacer />
            <BaseButton
              kind="primary"
              prepend-icon="mdi-eye-outline"
              :loading="quickLoading"
              :disabled="quickLoading || quickConfirming || !selectedQuickPdf"
              @click="emit('previewQuick')"
            >
              {{ t('spaces.detail.publishTask.quick.preview') }}
            </BaseButton>
          </div>
        </PanelCard>

        <!-- 解析预览结果：逐条摆出后端认出来的草稿。**只给看不给改** —— 参数在下面
             那张表单里填、`name` 由第一条草稿带过来；逐条能改的是「从 PDF 生成」那条路。 -->
        <PanelCard
          v-if="quickDrafts.length"
          data-testid="quick-drafts"
          :title="t('spaces.detail.publishTask.quick.resultTitle')"
          :subtitle="
            quickTokens !== null
              ? t('spaces.detail.publishTask.quick.resultSubtitleTokens', {
                  n: quickDrafts.length,
                  tokens: quickTokens,
                })
              : t('spaces.detail.publishTask.quick.resultSubtitle', { n: quickDrafts.length })
          "
        >
          <ul class="quick__list">
            <li v-for="(draft, index) in quickDrafts" :key="`${index}-${draft.name || 'draft'}`" class="quick__row">
              <div class="quick__row-head">
                <span class="quick__name"
                  >{{ index + 1 }}. {{ draft.name || t('spaces.detail.publishTask.quick.untitled') }}</span
                >
                <v-chip size="x-small" label variant="tonal" color="info">{{
                  t('spaces.detail.publishTask.quick.draftChip')
                }}</v-chip>
              </div>
              <p class="quick__intro">
                {{ t('spaces.detail.publishTask.quick.intro', { intro: draft.intro || '—' }) }}
              </p>
              <p class="quick__desc">
                {{ t('spaces.detail.publishTask.quick.description', { text: previewDescription(draft.description) }) }}
              </p>
            </li>
          </ul>

          <div class="pdf__actions">
            <i18n-t
              scope="global"
              keypath="spaces.detail.publishTask.quick.confirmNote"
              tag="span"
              class="pdf__actions-note"
            >
              <template #n>{{ quickDrafts.length }}</template>
              <template #queue
                ><b>{{ t('spaces.detail.publishTask.pendingQueue') }}</b></template
              >
            </i18n-t>
            <v-spacer />
            <BaseButton kind="ghost" :disabled="quickConfirming" @click="emit('clearQuick')">{{
              t('spaces.detail.publishTask.quick.clear')
            }}</BaseButton>
          </div>
        </PanelCard>

        <!-- 材料：选中即上传，发题那条请求带的是它的 id。 -->
        <TaskAttachmentPicker
          v-model:attachment-ids="attachmentIds"
          v-model:uploading="attachmentUploading"
          :load-limit="loadLimit"
          :upload="upload"
        />

        <!-- 这道题自己的「给 AI 队友的指导」（#944）：写了就盖过空间（与项目集）
             的默认，整份替换；六格全空就是不设，仍旧听空间的。两条发题路都带它。
             默认收起 —— 摊开着会挡住下面真正要填的那些格；收起时页头仍写着这一节
             是干什么的，普通题目不用点开。 -->
        <PanelCard
          data-testid="publish-teaching"
          :title="t('spaces.detail.publishTask.teaching.title')"
          :subtitle="t('spaces.detail.publishTask.teaching.subtitle')"
        >
          <template #actions>
            <BaseButton
              kind="ghost"
              size="sm"
              class="publish-teaching__toggle"
              data-testid="publish-teaching-toggle"
              :aria-expanded="teachingOpen"
              :append-icon="teachingOpen ? 'mdi-chevron-up' : 'mdi-chevron-down'"
              @click="teachingOpen = !teachingOpen"
            >
              {{
                t(
                  teachingOpen
                    ? 'spaces.detail.publishTask.teaching.collapse'
                    : 'spaces.detail.publishTask.teaching.expand'
                )
              }}
            </BaseButton>
          </template>
          <TeachingFields
            v-if="teachingOpen"
            v-model="teachingOverride"
            :materials="teachingMaterials"
            :materials-state="teachingMaterialsState"
            :library-to="spaceLibraryPath(spaceId)"
          />
        </PanelCard>

        <!-- 共享的发题表单（见容器文件头）。空间**装完再挂**：`v-if="ready"` 就是那件事。 -->
        <TaskForm
          v-if="ready"
          :initial-data="initialTaskData"
          :submit-button-text="t('tasks.publish.submit')"
          :classification-topics="classificationTopics"
          :categories="activeCategories"
          :selected-category-id="preselectedCategoryId"
          :domain-groups="domainGroups"
          :parameters-only="quickDrafts.length > 0"
          @submit="onSubmit"
        />
      </div>

      <aside class="pub__side">
        <!-- 这道题发出去之后会经过哪几站。一句话是给谁看的，跟着身份变。 -->
        <PanelCard :title="t('spaces.detail.publishTask.lifecycle.title')">
          <ol class="pub__steps" data-testid="publish-lifecycle">
            <li>
              <b>{{ t('spaces.detail.publishTask.pendingQueue') }}</b> ——
              {{ t('spaces.detail.publishTask.lifecycle.pending') }}
            </li>
            <li data-testid="publish-audience">
              <b>{{ t('spaces.detail.publishTask.lifecycle.reviewedLabel') }}</b> ——
              <template v-if="isManager">{{ t('spaces.detail.publishTask.lifecycle.reviewedManager') }}</template>
              <template v-else>{{ t('spaces.detail.publishTask.lifecycle.reviewedMember') }}</template>
            </li>
            <li>
              <b>{{ t('spaces.detail.publishTask.lifecycle.visibleLabel') }}</b> ——
              {{ t('spaces.detail.publishTask.lifecycle.visible') }}
            </li>
            <li>
              <b>{{ t('spaces.detail.publishTask.lifecycle.trackLabel') }}</b> ——
              {{ t('spaces.detail.publishTask.lifecycle.track') }}
            </li>
          </ol>
          <p class="pub__side-note">{{ t('spaces.detail.publishTask.lifecycle.rejectedNote') }}</p>
        </PanelCard>

        <!-- 现在提交得出去吗。清单里每一条都是底下那张表单**真会拦**的规则，
             由表单自己报上来（`lib/taskPublishChecks.ts`）。 -->
        <PanelCard :title="t('spaces.detail.publishTask.checks.title')">
          <ul v-if="formChecks?.length" class="pub__errors" data-testid="publish-checks">
            <li v-for="check in formChecks" :key="check.id">{{ check.text }}</li>
          </ul>
          <p v-else-if="formChecks" class="pub__ok" data-testid="publish-ok">
            {{ t('spaces.detail.publishTask.checks.ok') }}
          </p>
          <p v-else class="pub__wait" data-testid="publish-checks-waiting">
            {{ t('spaces.detail.publishTask.checks.waiting') }}
          </p>
          <BaseButton
            kind="primary"
            block
            :disabled="!formChecks || formChecks.length > 0"
            @click="emit('submitFromChecklist')"
          >
            {{ t('spaces.detail.publishTask.checks.submit') }}
          </BaseButton>
        </PanelCard>
      </aside>
    </div>

    <PdfGenerate
      v-else
      :pdf-template-index="pdfTemplateIndex"
      :teaching="teachingPayload"
      :space-id="spaceId"
      :current-space-id="currentSpaceId"
      :categories="categories"
      :preview-from-pdf="previewFromPdf"
      :confirm-from-pdf="confirmFromPdf"
    />
  </div>
</template>

<style scoped lang="scss">
@use '../../../styles/breakpoints.scss' as bp;

.pub__mode {
  flex: 0 0 auto;
}

/* 手写一道：左栏是这一页自己的卡片加那两件共享机器（见文件头），右栏那几张卡。
   窄屏（左边那一列是重表单）摞成一列。 */
.pub__grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  gap: 16px;
  align-items: start;
}

.pub__main {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

.pub__side {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.pub__steps {
  padding-left: 18px;
  margin: 0;
  font-size: 0.84rem;
  line-height: 1.9;
}

.pub__side-note {
  padding-top: 12px;
  margin: 12px 0 0;
  color: rgba(var(--v-theme-on-surface), 0.6);
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  font-size: 0.78rem;
  line-height: 1.7;
}

.pub__errors {
  padding-left: 18px;
  margin: 0 0 14px;
  color: rgb(var(--v-theme-error));
  font-size: 0.82rem;
  line-height: 1.8;
}

.pub__ok {
  margin: 0 0 14px;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.82rem;
}

.pub__wait {
  margin: 0 0 14px;
  color: rgba(var(--v-theme-on-surface), 0.5);
  font-size: 0.78rem;
  line-height: 1.7;
}

// 断点收进共享 token（`styles/breakpoints.scss`）：1100 → 1180（`$bp-compact`）。
@include bp.below(bp.$bp-compact) {
  .pub__grid {
    grid-template-columns: minmax(0, 1fr);
  }
}

/* ---- PDF 快速发布（手写一道那一栏）---- */

.quick__list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 0;
  margin: 0;
  list-style: none;
}

.quick__row {
  padding: 12px;
  background: rgba(var(--v-theme-on-surface), 0.03);
  border-radius: var(--radius-md);
}

.quick__row-head {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
}

.quick__name {
  font-size: 0.86rem;
  font-weight: 600;
}

.quick__intro,
.quick__desc {
  margin: 6px 0 0;
  color: rgba(var(--v-theme-on-surface), 0.62);
  font-size: 0.78rem;
  line-height: 1.7;
}

/* ---- 从 PDF 生成 ---- */

.pdf__hint {
  margin: 4px 0 0;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.8rem;
  line-height: 1.7;
}

.pdf__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  margin-top: 16px;
  padding-top: 14px;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.08);
}

.pdf__actions-note {
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.78rem;
}
</style>
