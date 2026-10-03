<script setup lang="ts">
// 发题页。两条路：手写一道（`TaskForm`，可以先从一份 PDF 解析出草稿再统一填发布参数），
// 或从一份 PDF 批量生成、逐条改完再发。两条路走同一批接口：`POST /tasks` 与
// `POST /tasks/publish/from-pdf/preview|confirm`。
//
// 表单要的分类、话题、域名组、模板都从 pinia 的 `space` store 来，这一页自己装，
// **装完才挂表单**：`fetchCategories()` 在 `currentSpaceId` 为空时直接返回，装晚了
// 分类下拉是空的。`?templateId=`（从「选择模板」过来）与 `?categoryId=`（从某个分类的
// 列表过来）都在这里读。
import type { PublishCheck } from '@/lib/taskPublishChecks'
import type { PdfTaskDraftData } from '@/network/api/tasks/types'
import type { SpaceTeaching, TaskFormSubmitData } from '@/types'

import { computed, defineAsyncComponent, provide, ref, shallowRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'
import { storeToRefs } from 'pinia'

import { useSpaceData } from '@/composables/useSpaceData'
import { useSpaceMaterials } from '@/composables/useSpaceMaterials'

import PdfGenerate from './PdfGenerate.vue'
import { MAX_DRAFTS, MAX_PDF_BYTES, TASK_SUBMISSION_SCHEMA } from './publishLimits'

import PageHeader from '@/components/common/PageHeader.vue'
import TeachingFields from '@/components/common/TeachingFields.vue'
import PanelCard from '@/components/spaces/PanelCard.vue'
import { publishDoneRoute, spaceLibraryPath } from '@/lib/spaceRouteNames'
import { PUBLISH_CHECKS_SINK } from '@/lib/taskPublishChecks'
import { isTeachingBlank } from '@/lib/teaching'
import { TasksApi } from '@/network/api/tasks'
import errorHandler from '@/services/ErrorHandler'
import { useSpaceStore } from '@/stores/space'

const TaskForm = defineAsyncComponent(() => import('@/components/tasks/TaskForm.vue'))
const TaskAttachmentPicker = defineAsyncComponent(() => import('@/components/tasks/TaskAttachmentPicker.vue'))

const route = useRoute()
const router = useRouter()
const { t } = useI18n()

const spaceStore = useSpaceStore()
const spaceData = useSpaceData()
const { currentSpaceId, templates, classificationTopics, categories, domainGroups, isManager } = storeToRefs(spaceStore)

const spaceId = computed(() => Number(route.params.spaceId))

/** 两条发题路：手写一道，或从一份 PDF 里批量生成。 */
const mode = ref<'write' | 'pdf'>('write')

// --- 装这块板（表单要的东西全在这里，见文件头「装完再挂」）----------------------

/** 装好之前不挂表单。 */
const ready = ref(false)

/** 把这一页要的那几样装上：空间自己、这块板的分类与域名组，以及地址栏点名的模板。
 *
 *  顺序有意义（见文件头「装完再挂」）：`fetchCategories()` 与 `fetchDomainGroups()`
 *  都挂在 `currentSpaceId` 上，空间没装好它们就是空操作 —— 不报错，但表单那一栏是空的。 */
async function loadSpace(id: number) {
  if (!Number.isFinite(id) || id <= 0) return
  ready.value = false
  await spaceData.fetchSpace(id)
  // 空间换了（或者用户直接输地址进来）：下面这几样都挂在 `currentSpaceId` 上，顺序有意义。
  if (currentSpaceId.value !== id) return
  const ok = await errorHandler.withErrorHandling(
    async () => {
      await spaceData.fetchCategories()
      await spaceData.fetchDomainGroups()
      await loadTemplate()
      // 回调的返回值就是「装完没有」：`withErrorHandling` 栽了给的是 `undefined`，
      // 所以这里必须回一个真东西，不能靠 `undefined` 判成败（回调什么都不返回时
      // 成功与失败是同一个值）。
      return true
    },
    { defaultMessage: t('spaces.detail.publishTask.initializationFailed') }
  )
  // 装配期间空间被换掉/页面被切走时不要再放行 —— 放行了表单拿到的就是别人的分类。
  if (ok === true && currentSpaceId.value === id) ready.value = true
}

watch(spaceId, (id) => void loadSpace(id), { immediate: true })

/** 参考资料那一格的候选。**不并进 `loadSpace` 的成败里** —— 取不到也照常发得了题
 *  （这一节默认还是收起的），选择器那边会说清是读不出来。 */
const { materials: teachingMaterials, state: teachingMaterialsState } = useSpaceMaterials(spaceId)

/** 活跃（未归档）的分类，按 `displayOrder` 排 —— 与老页同一口径：下拉里只有能选的。 */
const activeCategories = computed(() =>
  categories.value.filter((category) => !category.archivedAt).sort((a, b) => a.displayOrder - b.displayOrder)
)

/** `?categoryId=` 预选：不在活跃分类里就当没有（否则表单会拿一个选不出来的 id 去提交）。 */
const preselectedCategoryId = computed(() => {
  const param = route.query.categoryId
  if (!param) return undefined
  const id = Number(param)
  return activeCategories.value.some((cat) => cat.id === id) ? id : undefined
})

/** `?templateId=` 既喂表单（模板初值），也喂 PDF 解析（用哪份模板读 PDF），两条路同一口径。 */
const templateIdParam = computed(() => {
  const param = route.query.templateId
  if (!param || param === 'blank') return null
  const id = Number(param)
  return Number.isFinite(id) ? id : null
})

/** 模板索引。PDF 那条路要的是 `-1`（空白），见 `parsePdf`。 */
const pdfTemplateIndex = computed(() => templateIdParam.value ?? -1)

/** 表单初值：没模板就是空对象（表单自己那套默认值顶上）。 */
const initialTaskData = ref<Record<string, unknown>>({})

function loadTemplate() {
  const id = templateIdParam.value
  if (id === null) return
  const template = templates.value[id]
  if (!template) return
  initialTaskData.value = {
    name: template.title,
    description: JSON.parse(template.content),
    submitterType: template.submitterType !== null ? template.submitterType : undefined,
    rank: template.rank !== null ? template.rank : undefined,
    minTeamSize: template.minTeamSize,
    maxTeamSize: template.maxTeamSize,
    defaultDeadline: template.defaultDeadline !== null ? template.defaultDeadline : undefined,
    requireRealName: template.requireRealName !== null ? template.requireRealName : undefined,
  }
}

// --- 右栏两张卡 ---------------------------------------------------------------

/**
 * 「提交前」那张清单：底下那张真表单每变一次就报一次它现在**拦着你的**规则，
 * 报空数组就是提交得出去。规则表与这句话的出处写在 `lib/taskPublishChecks.ts`。
 *
 * `null` = 表单还没挂上（空间还在装、或者这会儿在 PDF 那条路上）—— 那时这张卡
 * 说不出「没问题」，所以按钮是灰的、也不画那一行。**不拿空数组顶上**：那是
 * 「校验过了」，跟「还没得可校验」不是一句话。
 */
const formChecks = shallowRef<PublishCheck[] | null>(null)
/** 表单交上来的它自己的提交。清单那颗「提交审核」按钮走的就是它。 */
const formSubmit = shallowRef<(() => void) | null>(null)

provide(PUBLISH_CHECKS_SINK, {
  report: (checks) => {
    formChecks.value = checks
  },
  handOverSubmit: (submit) => {
    formSubmit.value = submit
  },
})

/** 清单那颗按钮：真表单自己会拦的就让它拦（`handleSubmit` 校验不过什么都不发生）。 */
function submitFromChecklist() {
  formSubmit.value?.()
}

// --- 手写一道：附件与提交 ------------------------------------------------------

/** 随题一起发出的材料：附件卡片上传完拿到 id，发题那条请求带着它一起走。 */
const attachmentIds = ref<number[]>([])
const attachmentUploading = ref(false)

/** 这道题自己的「给 AI 队友的指导」覆盖（#944）。留空就什么都不发，用空间的默认。 */
const teachingOverride = ref<SpaceTeaching>({})

/** 那一节默认收起：多数题目一个字都不用写它，摊开着只是挡路。 */
const teachingOpen = ref(false)

/** 交给接口的那一份：六格全空就不带它 —— 让空间（或项目集）的默认生效，而不是写
 *  一份空的把下面那层盖住。两条发题路共用同一个判据。 */
const teachingPayload = computed(() => (isTeachingBlank(teachingOverride.value) ? undefined : teachingOverride.value))

/**
 * 提交。两条路走的是两个接口，但**入口只有一个**：表单的那颗提交按钮。
 *
 * - 解析过 PDF（`quickDrafts` 非空）→ 批量发布那条路（`confirm`）；
 * - 否则 → `POST /tasks` 建一道题。
 *
 * 发完落到「我发布的」。
 */
async function submitTask(taskData: TaskFormSubmitData) {
  const id = currentSpaceId.value
  if (!id) {
    toast.error(t('spaces.detail.publishTask.spaceIdNotFound'))
    return false
  }

  if (quickDrafts.value.length > 0) return (await confirmQuickFromPdf(taskData, id)) ?? false

  // 材料还在上传就先别发：附件 id 是发题那条请求的一部分，这时候发出去就会静默地
  // 少一份用户明明已经选好的文件。
  if (attachmentUploading.value) {
    toast.error(t('spaces.detail.publishTask.attachmentsUploading'))
    return false
  }

  const result = await errorHandler.withErrorHandling(
    async () => {
      const {
        data: {
          task: { approved },
        },
      } = await TasksApi.create({
        ...taskData,
        submissionSchema: TASK_SUBMISSION_SCHEMA,
        space: id,
        requireRealName: taskData.requireRealName || false,
        categoryId: taskData.categoryId,
        accessControlEnabled: taskData.accessControlEnabled || false,
        accessDomainGroupIds: taskData.accessControlEnabled ? taskData.accessDomainGroupIds : undefined,
        attachmentIds: attachmentIds.value.length > 0 ? attachmentIds.value : undefined,
        teaching: teachingPayload.value,
      })

      if (!approved) {
        toast.success(t('spaces.detail.publishTask.createSuccessAndWaitingAudit'))
      } else {
        toast.success(t('spaces.detail.publishTask.createSuccess'))
      }

      router.replace(publishDoneRoute(id))
      return approved
    },
    {
      defaultMessage: t('spaces.detail.publishTask.createFailed'),
    }
  )

  return result !== undefined
}

// --- 手写一道：PDF 快速发布（老页本来那张卡）-----------------------------------

const quickFile = ref<File | File[] | null>(null)
const quickLoading = ref(false)
const quickConfirming = ref(false)

/** 解析出来的草稿（`PdfTaskDraftData` 原样），以及这次烧掉多少 token。 */
const quickDrafts = ref<PdfTaskDraftData[]>([])
const quickTokens = ref<number | null>(null)

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

/** 解析预览：真的把这份 PDF 发给后端（`POST /tasks/publish/from-pdf/preview`）。 */
async function previewFromPdf() {
  const id = currentSpaceId.value
  const file = selectedQuickPdf.value
  if (!id) {
    toast.error(t('spaces.detail.publishTask.spaceIdNotFound'))
    return
  }
  if (!file) {
    toast.error(t('spaces.detail.publishTask.quick.noFile'))
    return
  }
  if (file.size > MAX_PDF_BYTES) {
    toast.error(t('spaces.detail.publishTask.quick.tooLarge'))
    return
  }

  quickLoading.value = true
  try {
    const { data } = await TasksApi.previewFromPdf({
      spaceId: id,
      file,
      categoryId: preselectedCategoryId.value,
      templateIndex: pdfTemplateIndex.value,
      maxTasks: MAX_DRAFTS,
    })

    if (!data.drafts || data.drafts.length === 0) {
      toast.error(t('spaces.detail.publishTask.quick.noDrafts'))
      quickDrafts.value = []
      quickTokens.value = data.tokenUsed ?? null
      initialTaskData.value = {}
      return
    }

    quickDrafts.value = data.drafts
    initialTaskData.value = { name: data.drafts[0]?.name || t('spaces.detail.publishTask.quick.parametersName') }
    quickTokens.value = data.tokenUsed ?? null
    toast.success(t('spaces.detail.publishTask.quick.parsed', { n: data.drafts.length }))
  } catch (error) {
    console.error('PDF 解析预览失败:', error)
    toast.error(t('spaces.detail.publishTask.quick.previewFailed'))
  } finally {
    quickLoading.value = false
  }
}

/** 清空预览：表单也从「只填参数」回到「手写一道」那张完整的表单。 */
function clearQuickDrafts() {
  quickDrafts.value = []
  quickTokens.value = null
  initialTaskData.value = {}
}

/** 批量发布那条路共用的一半参数（后端 `_apply_pdf_task_options` 把它与每条草稿合起来）。 */
function buildTaskOptions(taskData: TaskFormSubmitData, id: number) {
  return {
    ...taskData,
    submissionSchema: TASK_SUBMISSION_SCHEMA,
    space: id,
    name: taskData.name || t('spaces.detail.publishTask.quick.parametersName'),
    intro: '',
    description: '',
    requireRealName: taskData.requireRealName || false,
    categoryId: taskData.categoryId,
    accessControlEnabled: taskData.accessControlEnabled || false,
    accessDomainGroupIds: taskData.accessControlEnabled ? taskData.accessDomainGroupIds : undefined,
    teaching: teachingPayload.value,
  }
}

/** 确认并批量发布草稿（`POST /tasks/publish/from-pdf/confirm`），发完落到「我的」。 */
async function confirmQuickFromPdf(taskData: TaskFormSubmitData, id: number) {
  if (quickDrafts.value.length === 0) {
    toast.error(t('spaces.detail.publishTask.quick.nothingToPublish'))
    return false
  }

  quickConfirming.value = true
  try {
    const { data } = await TasksApi.confirmFromPdf({
      drafts: quickDrafts.value,
      taskOptions: buildTaskOptions(taskData, id),
    })
    toast.success(t('spaces.detail.publishTask.quick.published', { n: data.count || data.tasks.length }))
    quickDrafts.value = []
    quickTokens.value = null
    initialTaskData.value = {}
    router.replace(publishDoneRoute(id))
    return true
  } catch (error) {
    console.error('PDF 批量发布失败:', error)
    toast.error(t('spaces.detail.publishTask.quick.publishFailed'))
    return false
  } finally {
    quickConfirming.value = false
  }
}
</script>

<template>
  <PageHeader show-on-mobile>
    <template #actions>
      <v-btn-toggle v-model="mode" density="compact" variant="outlined" divided mandatory class="pub__mode">
        <v-btn value="write" size="small" prepend-icon="mdi-pencil-outline">{{
          t('spaces.detail.publishTask.mode.write')
        }}</v-btn>
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
            <v-btn
              color="primary"
              variant="flat"
              :loading="quickLoading"
              :disabled="quickLoading || quickConfirming || !selectedQuickPdf"
              @click="previewFromPdf"
            >
              <v-icon start>mdi-eye-outline</v-icon>
              {{ t('spaces.detail.publishTask.quick.preview') }}
            </v-btn>
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
            <v-btn variant="text" :disabled="quickConfirming" @click="clearQuickDrafts">{{
              t('spaces.detail.publishTask.quick.clear')
            }}</v-btn>
          </div>
        </PanelCard>

        <!-- 材料：选中即上传，发题那条请求带的是它的 id。 -->
        <TaskAttachmentPicker
          @update:attachment-ids="attachmentIds = $event"
          @update:uploading="attachmentUploading = $event"
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
            <v-btn
              size="small"
              variant="text"
              class="publish-teaching__toggle"
              data-testid="publish-teaching-toggle"
              :aria-expanded="teachingOpen"
              @click="teachingOpen = !teachingOpen"
            >
              {{
                t(
                  teachingOpen
                    ? 'spaces.detail.publishTask.teaching.collapse'
                    : 'spaces.detail.publishTask.teaching.expand'
                )
              }}
              <v-icon :icon="teachingOpen ? 'mdi-chevron-up' : 'mdi-chevron-down'" size="18" end />
            </v-btn>
          </template>
          <TeachingFields
            v-if="teachingOpen"
            v-model="teachingOverride"
            :materials="teachingMaterials"
            :materials-state="teachingMaterialsState"
            :library-to="spaceLibraryPath(spaceId)"
          />
        </PanelCard>

        <!-- 共享的发题表单（见文件头）。空间**装完再挂**：`v-if="ready"` 就是那件事。 -->
        <TaskForm
          v-if="ready"
          :initial-data="initialTaskData"
          :submit-button-text="t('tasks.publish.submit')"
          :classification-topics="classificationTopics"
          :categories="activeCategories"
          :selected-category-id="preselectedCategoryId"
          :domain-groups="domainGroups"
          :parameters-only="quickDrafts.length > 0"
          @submit="submitTask"
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
          <v-btn
            block
            color="primary"
            variant="flat"
            :disabled="!formChecks || formChecks.length > 0"
            @click="submitFromChecklist"
          >
            {{ t('spaces.detail.publishTask.checks.submit') }}
          </v-btn>
        </PanelCard>
      </aside>
    </div>

    <PdfGenerate v-else :pdf-template-index="pdfTemplateIndex" :teaching="teachingPayload" />
  </div>
</template>

<style scoped lang="scss">
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

@media (max-width: 1100px) {
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
