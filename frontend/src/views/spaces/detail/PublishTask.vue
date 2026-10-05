<script setup lang="ts">
// 发题页。两条路：手写一道（`TaskForm`，可以先从一份 PDF 解析出草稿再统一填发布参数），
// 或从一份 PDF 批量生成、逐条改完再发。两条路走同一批接口：`POST /tasks` 与
// `POST /tasks/publish/from-pdf/preview|confirm`。
//
// 表单要的分类、话题、域名组、模板都从 pinia 的 `space` store 来，这一页自己装，
// **装完才挂表单**：`fetchCategories()` 在 `currentSpaceId` 为空时直接返回，装晚了
// 分类下拉是空的。`?templateId=`（从「选择模板」过来）与 `?categoryId=`（从某个分类的
// 列表过来）都在这里读。
//
// 画那一半在 `PublishTaskView.vue`：这一层做装配、取数、发题与跳转，值经 props /
// v-model 递下去，那一件往上发事件。底下那几件共享机器（`TaskForm`、材料卡、
// `PdfGenerate`）自己不吃路由也不吃 store，接口由这一层当回调递进去。
import type { PublishCheck } from '@/lib/taskPublishChecks'
import type { PdfTaskDraftData } from '@/network/api/tasks/types'
import type { SpaceTeaching, TaskFormSubmitData } from '@/types'

import { computed, provide, ref, shallowRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'
import { storeToRefs } from 'pinia'

import { useSpaceData } from '@/composables/useSpaceData'
import { useSpaceMaterials } from '@/composables/useSpaceMaterials'

import { MAX_DRAFTS, MAX_PDF_BYTES, TASK_SUBMISSION_SCHEMA } from './publishLimits'
import PublishTaskView from './PublishTaskView.vue'

import { publishDoneRoute } from '@/lib/spaceRouteNames'
import { PUBLISH_CHECKS_SINK } from '@/lib/taskPublishChecks'
import { isTeachingBlank } from '@/lib/teaching'
import { AttachmentsApi } from '@/network/api/attachments'
import { TasksApi } from '@/network/api/tasks'
import errorHandler from '@/services/ErrorHandler'
import { useSpaceStore } from '@/stores/space'

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

/** 解析预览：真的把这份 PDF 发给后端（`POST /tasks/publish/from-pdf/preview`）。 */
async function previewQuick() {
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

// --- 递给视图的那几只手 ---------------------------------------------------------
//
// 底下那几件共享机器不吃路由、也不吃 store：接口在这一层接上，当回调递进去，
// 调用时机与从前一模一样（材料卡挂上时问一次上限；「从 PDF 生成」按下按钮那一刻
// 才发请求）。

/** 材料卡那句上限（`GET /attachments/limits`）。问不到给 `null`，不抛。 */
async function loadAttachmentLimit() {
  try {
    const { data } = await AttachmentsApi.limits()
    return data.maxFileBytes
  } catch {
    return null
  }
}

/** 材料卡传一份文件（`POST /attachments`），回来的是它在服务端那行的 id。 */
async function uploadAttachment(file: File) {
  const { data } = await AttachmentsApi.upload({ type: 'file', file })
  return { id: data.id }
}

/** 「从 PDF 生成」那条路的两个接口，原样透传。 */
const previewPdf = (payload: Parameters<typeof TasksApi.previewFromPdf>[0]) => TasksApi.previewFromPdf(payload)
const confirmPdf = (payload: Parameters<typeof TasksApi.confirmFromPdf>[0]) => TasksApi.confirmFromPdf(payload)
</script>

<template>
  <PublishTaskView
    v-model:mode="mode"
    v-model:quick-file="quickFile"
    v-model:teaching-open="teachingOpen"
    v-model:teaching-override="teachingOverride"
    v-model:attachment-ids="attachmentIds"
    v-model:attachment-uploading="attachmentUploading"
    :quick-loading="quickLoading"
    :quick-confirming="quickConfirming"
    :quick-drafts="quickDrafts"
    :quick-tokens="quickTokens"
    :ready="ready"
    :initial-task-data="initialTaskData"
    :classification-topics="classificationTopics"
    :active-categories="activeCategories"
    :preselected-category-id="preselectedCategoryId"
    :domain-groups="domainGroups"
    :is-manager="isManager"
    :form-checks="formChecks"
    :space-id="spaceId"
    :pdf-template-index="pdfTemplateIndex"
    :teaching-payload="teachingPayload"
    :teaching-materials="teachingMaterials"
    :teaching-materials-state="teachingMaterialsState"
    :categories="categories"
    :current-space-id="currentSpaceId"
    :load-limit="loadAttachmentLimit"
    :upload="uploadAttachment"
    :preview-from-pdf="previewPdf"
    :confirm-from-pdf="confirmPdf"
    @preview-quick="previewQuick"
    @clear-quick="clearQuickDrafts"
    @submit-task="submitTask"
    @submit-from-checklist="submitFromChecklist"
  />
</template>
