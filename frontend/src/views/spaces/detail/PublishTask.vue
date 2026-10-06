<script setup lang="ts">
// 发题页的容器：装空间、读文件、传附件、发布。画面在 `PublishTaskView.vue`。
//
// 发一道走 `POST /tasks`；从文件里读出好几道时走 `POST /tasks/publish/from-pdf/confirm`，
// 逐道的名称、描述、简介由草稿给，其余设置这一批共用。
//
// 表单要的分类、话题、域名组、模板都从 pinia 的 `space` store 来，这一页自己装，
// **装完才挂表单**：`fetchCategories()` 在 `currentSpaceId` 为空时直接返回，装晚了
// 分类下拉是空的。`?categoryId=`（从某个分类的列表过来）在这里读。
import type { PickedAttachment } from '@/composables/useAttachmentUploads'
import type { SpaceTeaching, TaskFormSubmitData } from '@/types'
import type { PublishDraft } from './publishDrafts'

import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'
import { storeToRefs } from 'pinia'

import { useAttachmentUploads } from '@/composables/useAttachmentUploads'
import { useSpaceData } from '@/composables/useSpaceData'
import { useSpaceMaterials } from '@/composables/useSpaceMaterials'
import { descriptionDoc } from '@/composables/useTaskForm'

import { toDraftPayload, toDrafts } from './publishDrafts'
import { MAX_DRAFTS, MAX_PDF_BYTES, TASK_SUBMISSION_SCHEMA } from './publishLimits'
import PublishTaskView from './PublishTaskView.vue'

import { publishDoneRoute, spaceLibraryPath } from '@/lib/spaceRouteNames'
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
const { currentSpaceId, templates, classificationTopics, categories, domainGroups } = storeToRefs(spaceStore)

const spaceId = computed(() => Number(route.params.spaceId))
const listTo = computed(() => ({ name: 'SpacesDetailTasksList', params: { spaceId: spaceId.value } }))

// --- 装这块板 ------------------------------------------------------------------

/** 装好之前不挂表单。 */
const ready = ref(false)

/** 顺序有意义（见文件头）：分类与域名组都挂在 `currentSpaceId` 上，空间没装好它们就是空操作。 */
async function loadSpace(id: number) {
  if (!Number.isFinite(id) || id <= 0) return
  ready.value = false
  await spaceData.fetchSpace(id)
  if (currentSpaceId.value !== id) return
  const ok = await errorHandler.withErrorHandling(
    async () => {
      await spaceData.fetchCategories()
      await spaceData.fetchDomainGroups()
      // 回调的返回值就是「装完没有」：栽了给的是 `undefined`。
      return true
    },
    { defaultMessage: t('spaces.detail.publishTask.initializationFailed') }
  )
  // 装配期间空间被换掉/页面被切走时不要再放行 —— 放行了表单拿到的就是别人的分类。
  if (ok === true && currentSpaceId.value === id) ready.value = true
}

watch(spaceId, (id) => void loadSpace(id), { immediate: true })

/** 参考资料那一格的候选。不并进 `loadSpace` 的成败里：取不到也照常发得了题。 */
const { materials, state: materialsState } = useSpaceMaterials(spaceId)

/** 活跃（未归档）的分类，按 `displayOrder` 排：下拉里只有能选的。 */
const activeCategories = computed(() =>
  categories.value.filter((category) => !category.archivedAt).sort((a, b) => a.displayOrder - b.displayOrder)
)

/** `?categoryId=` 预选：不在活跃分类里就当没有（否则表单会拿一个选不出来的 id 去提交）。 */
const preselectedCategoryId = computed(() => {
  const id = Number(route.query.categoryId)
  return activeCategories.value.some((category) => category.id === id) ? id : undefined
})

// --- 表单初值：空白、模板、或者从文件里读出的那一道 --------------------------------

const initialData = ref<Partial<TaskFormSubmitData>>({})
/** 换一份初值就重挂表单。 */
const formKey = ref(0)
/** 读文件时用哪份模板；没用模板就是空白（`-1`）。 */
const templateIndex = ref(-1)

const templateOptions = computed(() =>
  templates.value.map((template, index) => ({ index, name: template.name, description: template.description }))
)

function useTemplate(index: number) {
  const template = templates.value[index]
  if (!template) return
  templateIndex.value = index
  initialData.value = {
    name: template.title,
    description: template.content,
    submitterType: template.submitterType ?? undefined,
    rank: template.rank ?? undefined,
    minTeamSize: template.minTeamSize,
    maxTeamSize: template.maxTeamSize,
    defaultDeadline: template.defaultDeadline ?? undefined,
    requireRealName: template.requireRealName ?? undefined,
  }
  formKey.value++
}

// --- 从文件导入 ------------------------------------------------------------------

const importing = ref(false)
const importedFile = ref<string | null>(null)
const drafts = ref<PublishDraft[]>([])

async function importFile(file: File) {
  const id = currentSpaceId.value
  if (!id) return
  if (file.type !== 'application/pdf' && !file.name.toLowerCase().endsWith('.pdf')) {
    toast.error(t('spaces.detail.publishTask.importErrors.notPdf'))
    return
  }
  if (file.size > MAX_PDF_BYTES) {
    toast.error(t('spaces.detail.publishTask.importErrors.tooLarge'))
    return
  }

  importing.value = true
  try {
    const { data } = await TasksApi.previewFromPdf({
      spaceId: id,
      file,
      categoryId: preselectedCategoryId.value,
      templateIndex: templateIndex.value,
      maxTasks: MAX_DRAFTS,
    })
    if (!data.drafts?.length) {
      toast.error(t('spaces.detail.publishTask.importErrors.noDrafts'))
      return
    }

    // 服务端顺带落好的原 PDF 与插图，直接放进附件：不要的可以去掉。
    const imported: PickedAttachment[] = [
      ...(data.attachments?.pdf ? [data.attachments.pdf] : []),
      ...(data.attachments?.images ?? []),
    ].map(({ id: fileId, name, size }) => ({ id: fileId, name, size }))
    const known = new Set(uploads.files.value.map((existing) => existing.id))
    uploads.reset([...uploads.files.value, ...imported.filter((item) => !known.has(item.id))])

    importedFile.value = file.name
    const read = toDrafts(data.drafts, descriptionDoc)
    if (read.length === 1) {
      // 只有一道：就是这一道题的内容，接着在同一张表上改。
      drafts.value = []
      initialData.value = { ...initialData.value, name: read[0].name, description: JSON.stringify(read[0].description) }
      formKey.value++
    } else {
      drafts.value = read
    }
  } catch (error) {
    toast.error(
      t('spaces.detail.publishTask.importErrors.failed', {
        reason: error instanceof Error ? error.message : t('tasks.submit.unknownError'),
      })
    )
  } finally {
    importing.value = false
  }
}

function discardImport() {
  drafts.value = []
  importedFile.value = null
}

// --- 附件与 AI 指导 --------------------------------------------------------------

/** 发题时先传成游离的附件，建题那条请求带着它们的 id。 */
const uploads = useAttachmentUploads({
  upload: async (file) => (await AttachmentsApi.upload({ type: 'file', file })).data.id,
})

/** 这道题自己的「给 AI 队友的指导」覆盖（#944）。不单独写就沿用空间（或项目集）的默认。 */
const teaching = ref<SpaceTeaching>({})
const teachingOwn = ref(false)
const teachingPayload = computed(() =>
  teachingOwn.value && !isTeachingBlank(teaching.value) ? teaching.value : undefined
)

// --- 发布 --------------------------------------------------------------------------

const publishing = ref(false)

async function publish(form: TaskFormSubmitData) {
  const id = currentSpaceId.value
  if (!id || publishing.value) return
  // 材料还在上传就先别发：附件 id 是发题那条请求的一部分，这时发出去会静默少一份。
  if (uploads.uploading.value) {
    toast.error(t('spaces.detail.publishTask.attachmentsUploading'))
    return
  }

  const options = {
    ...form,
    submissionSchema: TASK_SUBMISSION_SCHEMA,
    space: id,
    attachmentIds: uploads.ids.value.length ? uploads.ids.value : undefined,
    teaching: teachingPayload.value,
  }

  publishing.value = true
  try {
    const picked = drafts.value.filter((draft) => draft.picked)
    if (picked.length > 1 || drafts.value.length > 1) {
      const payloads = picked.map((draft) => toDraftPayload(draft, id))
      const { data } = await TasksApi.confirmFromPdf({
        drafts: payloads,
        taskOptions: { ...options, name: payloads[0].name, description: payloads[0].description },
      })
      toast.success(t('spaces.detail.publishTask.publishedMany', { n: data.count || data.tasks.length }))
    } else {
      const {
        data: {
          task: { approved },
        },
      } = await TasksApi.create(options)
      toast.success(
        approved
          ? t('spaces.detail.publishTask.createSuccess')
          : t('spaces.detail.publishTask.createSuccessAndWaitingAudit')
      )
    }
    await router.replace(publishDoneRoute(id))
  } catch (error) {
    void errorHandler.handle(error as Error, { defaultMessage: t('spaces.detail.publishTask.createFailed') })
  } finally {
    publishing.value = false
  }
}
</script>

<template>
  <PublishTaskView
    v-model:drafts="drafts"
    v-model:teaching="teaching"
    v-model:teaching-own="teachingOwn"
    :ready="ready"
    :list-to="listTo"
    :form-key="formKey"
    :initial-data="initialData"
    :classification-topics="classificationTopics"
    :categories="activeCategories"
    :selected-category-id="preselectedCategoryId"
    :domain-groups="domainGroups"
    :templates="templateOptions"
    :importing="importing"
    :imported-file="importedFile"
    :attachments="uploads.files.value"
    :uploading="uploads.uploading.value"
    :max-file-bytes="uploads.maxFileBytes.value"
    :materials="materials"
    :materials-state="materialsState"
    :library-to="spaceLibraryPath(spaceId)"
    :publishing="publishing"
    @cancel="router.push(listTo)"
    @use-template="useTemplate"
    @import="importFile"
    @discard-import="discardImport"
    @add-attachments="uploads.add"
    @remove-attachment="uploads.remove"
    @submit="publish"
  />
</template>
