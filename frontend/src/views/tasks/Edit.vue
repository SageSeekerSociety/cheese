<script setup lang="ts">
// 改题页的容器：读地址、取题、装空间、传附件、保存、重新提交、跳转都在这儿；画面在
// `EditView.vue`。
//
// 和发题页同一张表（`TaskForm`），参与方式改不了，附件直接传到这道题上、从这道题上摘，
// AI 指导和发题时一样单独写或沿用空间的默认。没有导入和模板。
import type { SpaceTeaching, TaskFormSubmitData } from '@/types'

import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'
import { storeToRefs } from 'pinia'

import { useAttachmentUploads } from '@/composables/useAttachmentUploads'
import { useSpaceData } from '@/composables/useSpaceData'
import { useSpaceMaterials } from '@/composables/useSpaceMaterials'

import { useTaskData } from './composables'
import EditView from './EditView.vue'

import { closeOverlay } from '@/lib/backOut'
import { spaceLibraryPath } from '@/lib/spaceRouteNames'
import { isTeachingBlank } from '@/lib/teaching'
import { TasksApi } from '@/network/api/tasks'
import errorHandler from '@/services/ErrorHandler'
import { useSpaceStore } from '@/stores/space'

const { t } = useI18n()

const router = useRouter()
const route = useRoute()
const taskId = Number(route.params.taskId)

const { taskData, editTaskData, loading, error, loadTaskData } = useTaskData()

const spaceStore = useSpaceStore()
const spaceData = useSpaceData()
const { domainGroups, categories } = storeToRefs(spaceStore)

const spaceId = computed(() => taskData.value?.space?.id ?? Number(route.params.spaceId))
const listTo = computed(() => ({ name: 'SpacesDetailTasksList', params: { spaceId: spaceId.value } }))
const detailTo = computed(() => ({ name: 'TasksDetail', params: { spaceId: spaceId.value, taskId } }))

/** 活跃（未归档）的分类，加上这道题现在所在的那一个（哪怕已经归档，也得显示得出来）。 */
const editCategories = computed(() =>
  categories.value
    .filter((category) => !category.archivedAt || category.id === taskData.value?.category?.id)
    .sort((a, b) => a.displayOrder - b.displayOrder)
)

/** 题所属空间的话题；空间还没取到时是空的。 */
const classificationTopics = computed(() => taskData.value?.space?.classificationTopics ?? [])

const { materials, state: materialsState } = useSpaceMaterials(spaceId)

/** 附件直接传到这道题上、从这道题上摘：改题页上的增删当场生效，不等保存。 */
const uploads = useAttachmentUploads({
  upload: async (file) => (await TasksApi.uploadAttachment(taskId, file)).data.attachment.id,
  remove: async (id) => {
    await TasksApi.removeAttachment(taskId, id)
  },
})

/** 这道题自己的「给 AI 队友的指导」覆盖。不单独写就沿用空间（或项目集）的默认。 */
const teaching = ref<SpaceTeaching>({})
const teachingOwn = ref(false)

const saving = ref(false)

/** 审核没通过的题，改完可以一并重新提交审核。 */
const rejected = computed(() => taskData.value?.approved === 'DISAPPROVED')

/** 表单发上来的那一份。`resubmit` 是视图传下来的：刚刚按的是「保存并重新提交」。 */
async function onSubmit(data: TaskFormSubmitData, resubmit: boolean) {
  if (saving.value) return
  saving.value = true
  try {
    await TasksApi.update(taskId, {
      ...data,
      // 整份替换：沿用默认就交一份空的，让空间（或项目集）那一层重新生效。
      teaching: teachingOwn.value && !isTeachingBlank(teaching.value) ? teaching.value : {},
    })
    if (resubmit) {
      await TasksApi.resubmitTask(taskId)
      toast.success(t('tasks.edit.resubmitted'))
    } else {
      toast.success(t('tasks.detail.updateSuccess'))
    }
    navigateToDetail()
  } catch (err) {
    void errorHandler.handle(err as Error, { defaultMessage: t('global.updateFailed') })
  } finally {
    saving.value = false
  }
}

// 保存 / 取消之后回题目详情。**不是 push**：进来时就是从详情 push 过来的，出去再 push
// 一次，身后就多一条详情，按 ← 会落回那张刚保存过的表单。去向是定的（详情），所以走
// closeOverlay：身后正是它就退一格，否则 replace 过去。
const navigateToDetail = () => closeOverlay(router, detailTo.value)

onMounted(async () => {
  await loadTaskData()
  const task = taskData.value
  if (!task) return
  teaching.value = { ...(task.teaching ?? {}) }
  teachingOwn.value = !isTeachingBlank(teaching.value)
  if (task.space?.id) {
    spaceStore.setCurrentSpaceId(task.space.id)
    await Promise.all([spaceData.fetchDomainGroups(), spaceData.fetchCategories()])
  }
  try {
    const { data } = await TasksApi.listAttachments(taskId)
    uploads.reset(data.attachments.map(({ id, name, size }) => ({ id, name, size })))
  } catch {
    // 读不出来就当没有：增删仍然走得通，只是这一页不列已有的那几份。
  }
})
</script>

<template>
  <EditView
    v-model:teaching="teaching"
    v-model:teaching-own="teachingOwn"
    :loading="loading"
    :error="error"
    :has-task-data="taskData !== null"
    :task-name="taskData?.name ?? ''"
    :rejected="rejected"
    :saving="saving"
    :list-to="listTo"
    :detail-to="detailTo"
    :initial-data="editTaskData"
    :classification-topics="classificationTopics"
    :categories="editCategories"
    :domain-groups="domainGroups"
    :materials="materials"
    :materials-state="materialsState"
    :library-to="spaceLibraryPath(spaceId)"
    :attachments="uploads.files.value"
    :uploading="uploads.uploading.value"
    :max-file-bytes="uploads.maxFileBytes.value"
    @retry="loadTaskData"
    @cancel="navigateToDetail"
    @submit="onSubmit"
    @add-attachments="uploads.add"
    @remove-attachment="uploads.remove"
  />
</template>
