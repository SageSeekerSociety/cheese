<template>
  <v-sheet flat rounded="lg">
    <v-card flat rounded="lg" class="ma-4 mb-4 form-card">
      <v-card-item>
        <template #prepend>
          <v-avatar color="primary-lighten-5" size="44" class="elevation-0">
            <v-icon color="primary" size="24">mdi-file-pdf-box</v-icon>
          </v-avatar>
        </template>
        <v-card-title class="text-h6 ps-0">PDF 快速发布</v-card-title>
        <v-card-subtitle class="ps-0">上传赛题 PDF 后，系统会解析赛题内容；发布参数请在下方表单统一填写</v-card-subtitle>
      </v-card-item>

      <v-card-text class="pt-2">
        <v-file-input
          v-model="pdfFile"
          accept=".pdf,application/pdf"
          label="上传赛题 PDF"
          variant="outlined"
          density="comfortable"
          clearable
          prepend-icon=""
          :disabled="pdfPreviewLoading || pdfConfirmLoading"
          hide-details="auto"
        >
          <template #prepend>
            <v-icon color="primary" class="mr-2">mdi-upload</v-icon>
          </template>
          <template #selection="{ fileNames }">
            <v-chip color="primary" variant="outlined" label class="mt-1">
              <v-icon start>mdi-file-pdf-box</v-icon>
              {{ fileNames[0] }}
            </v-chip>
          </template>
        </v-file-input>

        <v-alert class="mt-3" type="info" variant="tonal" density="comfortable" border="start">
          支持 PDF 文件，单文件大小不超过 15MB。若当前 URL 带有模板参数，会优先使用该模板；否则使用空白模板。
        </v-alert>
      </v-card-text>

      <v-card-actions class="px-4 pb-4 pt-0 d-flex justify-end">
        <v-btn
          color="primary"
          :loading="pdfPreviewLoading"
          :disabled="pdfPreviewLoading || !selectedPdf"
          @click="previewFromPdf"
        >
          <v-icon start>mdi-eye-outline</v-icon>
          解析预览
        </v-btn>
      </v-card-actions>
    </v-card>

    <v-card v-if="pdfDrafts.length > 0" flat rounded="lg" class="ma-4 mb-4 form-card">
      <v-card-item>
        <template #prepend>
          <v-avatar color="success-lighten-5" size="44" class="elevation-0">
            <v-icon color="success" size="24">mdi-file-document-multiple-outline</v-icon>
          </v-avatar>
        </template>
        <v-card-title class="text-h6 ps-0">解析预览结果</v-card-title>
        <v-card-subtitle class="ps-0">
          共识别 {{ pdfDrafts.length }} 个赛题草稿，提交下方表单后会批量应用发布参数。
          <span v-if="pdfTokenUsed !== null">本次约消耗 {{ pdfTokenUsed }} tokens</span>
        </v-card-subtitle>
      </v-card-item>

      <v-card-text class="pt-0">
        <v-expansion-panels variant="accordion">
          <v-expansion-panel v-for="(draft, index) in pdfDrafts" :key="`${index}-${draft.name || 'draft'}`">
            <v-expansion-panel-title>
              <div class="d-flex align-center justify-space-between w-100 pr-2">
                <div class="text-subtitle-2">{{ index + 1 }}. {{ draft.name || '未命名赛题' }}</div>
                <v-chip size="x-small" color="primary" variant="tonal">PDF 草稿</v-chip>
              </div>
            </v-expansion-panel-title>
            <v-expansion-panel-text>
              <div class="text-body-2 mb-2"><strong>简介：</strong>{{ draft.intro || '-' }}</div>
              <div class="text-body-2 pdf-description-preview">
                <strong>内容预览：</strong>{{ previewDescription(draft.description) }}
              </div>
            </v-expansion-panel-text>
          </v-expansion-panel>
        </v-expansion-panels>
      </v-card-text>

      <v-card-actions class="px-4 pb-4 pt-0 d-flex justify-end">
        <v-btn variant="text" :disabled="pdfConfirmLoading" @click="clearPdfDrafts">清空预览</v-btn>
        <v-chip color="success" variant="tonal" label>
          <v-icon start>mdi-check-circle-outline</v-icon>
          下方发布按钮将批量发布
        </v-chip>
      </v-card-actions>
    </v-card>

    <task-form
      v-if="loadedTemplate"
      class="ma-4 pb-4"
      :initial-data="initialTaskData"
      :submit-button-text="t('tasks.publish.submit')"
      :classification-topics="classificationTopics"
      :categories="activeCategories"
      :selected-category-id="preselectedCategoryId"
      :domain-groups="domainGroups"
      :parameters-only="pdfDrafts.length > 0"
      @submit="submitTask"
    />
  </v-sheet>
</template>

<script setup lang="ts">
import type { TaskSubmissionSchemaEntry } from '@/types'
import type { TaskFormSubmitData } from '@/types'

import { computed, defineAsyncComponent, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'
import { storeToRefs } from 'pinia'

import { TasksApi } from '@/network/api/tasks'
import errorHandler from '@/services/ErrorHandler'
import { useSpaceStore } from '@/stores/space'

const TaskForm = defineAsyncComponent(() => import('@/components/tasks/TaskForm.vue'))

const router = useRouter()
const route = useRoute()
const { t } = useI18n()

const spaceStore = useSpaceStore()
const { currentSpaceId, templates, classificationTopics, categories, domainGroups } = storeToRefs(spaceStore)
const pdfFile = ref<File | File[] | null>(null)
const pdfPreviewLoading = ref(false)
const pdfConfirmLoading = ref(false)
const pdfDrafts = ref<Record<string, any>[]>([])
const pdfTokenUsed = ref<number | null>(null)

/** 当前选中的 PDF 文件（兼容 v-file-input 的单文件或数组返回值） */
const selectedPdf = computed<File | null>(() => {
  if (Array.isArray(pdfFile.value)) {
    return pdfFile.value[0] ?? null
  }
  return pdfFile.value
})

/** 获取活跃（未归档）的分类列表，按 displayOrder 排序 */
const activeCategories = computed(() => {
  return categories.value.filter((category) => !category.archivedAt).sort((a, b) => a.displayOrder - b.displayOrder)
})

/** 从 URL 查询参数中获取预选的分类 ID，并验证其是否在活跃分类中 */
const preselectedCategoryId = computed(() => {
  const categoryParam = route.query.categoryId
  if (!categoryParam) return undefined

  const categoryId = Number(categoryParam)
  // 确保分类在活跃分类列表中
  return activeCategories.value.some((cat) => cat.id === categoryId) ? categoryId : undefined
})

/** 从 URL 查询参数中获取 PDF 解析使用的模板索引，-1 表示空白模板 */
const pdfTemplateIndex = computed(() => {
  const templateParam = route.query.templateId
  if (!templateParam || templateParam === 'blank') return -1

  const templateId = Number(templateParam)
  return Number.isFinite(templateId) ? templateId : -1
})

const loadedTemplate = ref(false)

const initialTaskData = ref({})

const taskSubmissionSchema = ref<TaskSubmissionSchemaEntry[]>([
  {
    prompt: '提交文件',
    type: 'FILE',
  },
])

// const addSchemaEntry = (type: TaskSubmissionEntryType) => {
//   taskSubmissionSchema.value.push({ prompt: '', type })
// }

// const removeSchemaEntry = (index: number) => {
//   taskSubmissionSchema.value.splice(index, 1)
// }

/**
 * 提交赛题表单，调用 API 创建赛题
 * @param taskData - 表单填写的赛题数据
 * @returns 是否提交成功
 */
const submitTask = async (taskData: TaskFormSubmitData) => {
  const spaceId = currentSpaceId.value
  if (!spaceId) {
    toast.error(t('spaces.detail.publishTask.spaceIdNotFound'))
    return
  }

  if (pdfDrafts.value.length > 0) {
    return (await confirmPublishFromPdf(taskData, spaceId)) ?? false
  }

  const result = await errorHandler.withErrorHandling(
    async () => {
      const {
        data: {
          task: { approved },
        },
      } = await TasksApi.create({
        ...taskData,
        submissionSchema: taskSubmissionSchema.value,
        space: spaceId,
        requireRealName: taskData.requireRealName || false,
        categoryId: taskData.categoryId,
        accessControlEnabled: taskData.accessControlEnabled || false,
        accessDomainGroupIds: taskData.accessControlEnabled ? taskData.accessDomainGroupIds : undefined,
      })

      if (!approved) {
        toast.success(t('spaces.detail.publishTask.createSuccessAndWaitingAudit'))
      } else {
        toast.success(t('spaces.detail.publishTask.createSuccess'))
      }

      router.replace({ name: 'SpacesDetailMyPublishing', params: { spaceId } })
      return approved
    },
    {
      defaultMessage: t('spaces.detail.publishTask.createFailed'),
    }
  )

  return result !== undefined
}

/**
 * 上传 PDF 并请求后端解析预览
 * 校验文件大小（≤15MB），调用 previewFromPdf API 获取草稿列表
 */
const previewFromPdf = async () => {
  const spaceId = currentSpaceId.value
  if (!spaceId) {
    toast.error(t('spaces.detail.publishTask.spaceIdNotFound'))
    return
  }

  if (!selectedPdf.value) {
    toast.error('请先选择要上传的 PDF 文件')
    return
  }

  if (selectedPdf.value.size > 15 * 1024 * 1024) {
    toast.error('PDF 文件不能超过 15MB')
    return
  }

  pdfPreviewLoading.value = true
  try {
    const { data } = await TasksApi.previewFromPdf({
      spaceId,
      file: selectedPdf.value,
      categoryId: preselectedCategoryId.value,
      templateIndex: pdfTemplateIndex.value,
      maxTasks: 20,
    })

    if (!data.drafts || data.drafts.length === 0) {
      toast.error('未识别到可发布的赛题草稿')
      pdfDrafts.value = []
      pdfTokenUsed.value = data.tokenUsed ?? null
      initialTaskData.value = {}
      return
    }

    pdfDrafts.value = data.drafts
    initialTaskData.value = {
      name: data.drafts[0]?.name || 'PDF 批量发布参数',
    }
    pdfTokenUsed.value = data.tokenUsed ?? null
    toast.success(`解析完成，共识别 ${data.drafts.length} 个赛题草稿`)
  } catch (error) {
    console.error('PDF 解析预览失败:', error)
    toast.error('PDF 解析预览失败')
  } finally {
    pdfPreviewLoading.value = false
  }
}

/**
 * 确认并批量发布 PDF 解析出的赛题草稿
 * 调用 confirmFromPdf API，成功后跳转到「我发布的」页面
 */
const confirmPublishFromPdf = async (taskData: TaskFormSubmitData, spaceId: number) => {
  if (pdfDrafts.value.length === 0) {
    toast.error('没有可发布的草稿，请先解析预览')
    return false
  }

  pdfConfirmLoading.value = true
  try {
    const { data } = await TasksApi.confirmFromPdf({
      drafts: pdfDrafts.value,
      taskOptions: buildTaskOptions(taskData, spaceId),
    })
    toast.success(`已发布 ${data.count || data.tasks.length} 个赛题`)
    pdfDrafts.value = []
    pdfTokenUsed.value = null
    router.replace({ name: 'SpacesDetailMyPublishing', params: { spaceId } })
    return true
  } catch (error) {
    console.error('PDF 批量发布失败:', error)
    toast.error('PDF 批量发布失败')
    return false
  } finally {
    pdfConfirmLoading.value = false
  }
}

/** 清空当前的 PDF 预览草稿列表和 token 消耗记录 */
const clearPdfDrafts = () => {
  pdfDrafts.value = []
  pdfTokenUsed.value = null
  initialTaskData.value = {}
}

/** 生成 PDF 草稿内容预览文本。 */
const previewDescription = (value: unknown) => {
  if (!value) return '-'
  const text = String(value).replace(/\s+/g, ' ').trim()
  return text.length > 180 ? `${text.slice(0, 180)}...` : text || '-'
}

const buildTaskOptions = (taskData: TaskFormSubmitData, spaceId: number) => ({
  ...taskData,
  submissionSchema: taskSubmissionSchema.value,
  space: spaceId,
  name: taskData.name || 'PDF 批量发布参数',
  intro: '',
  description: '',
  requireRealName: taskData.requireRealName || false,
  categoryId: taskData.categoryId,
  accessControlEnabled: taskData.accessControlEnabled || false,
  accessDomainGroupIds: taskData.accessControlEnabled ? taskData.accessDomainGroupIds : undefined,
})

onMounted(async () => {
  await errorHandler.withErrorHandling(
    async () => {
      await spaceStore.fetchCategories()
      await fetchDomainGroups()
      const templateId = route.query.templateId
      if (templateId && templateId !== 'blank') {
        await loadTemplate(Number(templateId))
      }
      loadedTemplate.value = true
    },
    {
      defaultMessage: t('spaces.detail.publishTask.initializationFailed'),
    }
  )
})

const fetchDomainGroups = async () => {
  if (!currentSpaceId.value) return
  await spaceStore.fetchDomainGroups()
}

/**
 * 根据模板 ID 加载模板数据并填充表单初始值
 * @param templateId - 模板 ID
 */
const loadTemplate = async (templateId: number) => {
  const template = templates.value[templateId]
  if (template) {
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
}
</script>

<style scoped>
.form-card {
  border: 1px solid rgba(var(--v-border-color), 0.12);
  background-color: rgb(var(--v-theme-surface));
  transition: all 0.2s ease;
}

.form-card:hover {
  border-color: rgba(var(--v-theme-primary), 0.15);
  transform: translateY(-1px);
  box-shadow: 0 2px 8px rgba(var(--v-theme-primary), 0.05);
}
</style>
