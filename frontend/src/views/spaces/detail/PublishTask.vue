<script setup lang="ts">
// 发题页的容器：装空间、开草稿、边写边存、和芝士一起写、发布。画面在 `PublishTaskView.vue`。
//
// 题目写成草稿存在服务端（`useTaskDraft`）：地址栏 `?draft=` 记着眼前这份，刷新、换设备
// 都能接着写。刚打开、还没写一个字时不建草稿；第一次改动或第一次问芝士才建。芝士在右边，
// 它写这份草稿时表单跟着动（`useDraftAgent`）。发布就是把草稿发出去：先把表单校验过的
// 样子存进草稿，再由服务端照草稿发布，和人点「发布」、芝士调发布工具走的是同一条路。
//
// 表单要的分类、话题、域名组、模板都从 pinia 的 `space` store 来，这一页自己装，**装完才
// 挂表单**：`fetchCategories()` 在 `currentSpaceId` 为空时直接返回，装晚了分类下拉是空的。
// `?categoryId=`（从某个分类的列表过来）在这里读。
import type { AgentBox, AgentMark, AgentPointer } from '@/components/tasks/AgentLayer.vue'
import type { SpaceTeaching, TaskDraft, TaskDraftFields, TaskDraftSummary, TaskFormSubmitData } from '@/types'

import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'
import { storeToRefs } from 'pinia'

import { useAgentPointer } from '@/composables/useAgentPointer'
import { useAttachmentUploads } from '@/composables/useAttachmentUploads'
import { useDraftAgent } from '@/composables/useDraftAgent'
import { useSpaceData } from '@/composables/useSpaceData'
import { useSpaceMaterials } from '@/composables/useSpaceMaterials'
import { useTaskDraft } from '@/composables/useTaskDraft'

import PublishTaskView from './PublishTaskView.vue'

import { AttachmentsApi } from '@/api/attachments'
import { TaskDraftsApi } from '@/api/taskDrafts'
import AssistantPanel from '@/components/assistant/AssistantPanel.vue'
import { publishDoneRoute, spaceLibraryPath } from '@/lib/spaceRouteNames'
import { mentorOptions } from '@/lib/taskMentors'
import { isTeachingBlank } from '@/lib/teaching'
import { myAccount, myHandle } from '@/me'
import errorHandler from '@/services/ErrorHandler'
import { useSpaceStore } from '@/stores/space'
import { draftFieldsOf } from '@/types'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()

const spaceStore = useSpaceStore()
const spaceData = useSpaceData()
const { currentSpaceId, templates, classificationTopics, categories, domainGroups } = storeToRefs(spaceStore)

const spaceId = computed(() => Number(route.params.spaceId))
const listTo = computed(() => ({ name: 'SpacesDetailTasksList', params: { spaceId: spaceId.value } }))
const draftParam = computed(() => {
  const id = Number(route.query.draft)
  return Number.isFinite(id) && id > 0 ? id : null
})

// --- 装这块板 ------------------------------------------------------------------

/** 空间装好之前不挂表单。 */
const spaceReady = ref(false)

/** 顺序有意义（见文件头）：分类与域名组都挂在 `currentSpaceId` 上，空间没装好它们就是空操作。 */
async function loadSpace(id: number) {
  if (!Number.isFinite(id) || id <= 0) return
  spaceReady.value = false
  await spaceData.fetchSpace(id)
  if (currentSpaceId.value !== id) return
  const ok = await errorHandler.withErrorHandling(
    async () => {
      await spaceData.fetchCategories()
      await spaceData.fetchDomainGroups()
      return true
    },
    { defaultMessage: t('spaces.detail.publishTask.initializationFailed') }
  )
  if (ok === true && currentSpaceId.value === id) spaceReady.value = true
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

/** 芝士换算分类、标签名字用的。 */
const terms = () => ({
  categories: activeCategories.value.map((c) => ({ id: c.id, name: c.name })),
  topics: classificationTopics.value.map((topic) => ({ id: topic.id, name: topic.name })),
})

// --- 草稿 ------------------------------------------------------------------------

const draft = useTaskDraft({
  spaceId: () => currentSpaceId.value ?? spaceId.value,
  started: (row) => {
    void router.replace({ query: { ...route.query, draft: String(row.id) } })
    void refreshDrafts()
  },
})

const drafts = ref<TaskDraftSummary[]>([])
async function refreshDrafts() {
  try {
    drafts.value = await TaskDraftsApi.list(spaceId.value)
  } catch {
    // 列表读不出来不挡着写题：顶栏的「草稿」暂时是空的。
  }
}

const draftReady = ref(false)
const initialData = ref<Partial<TaskFormSubmitData>>({})
/** 换一份初值就重挂表单。 */
const formKey = ref(0)
const publishedHere = ref(false)

/** 不在表单里、但也存进草稿的几样：附件、AI 指导。 */
const teaching = ref<SpaceTeaching>({})
const teachingOwn = ref(false)
const uploads = useAttachmentUploads({
  upload: async (file) => (await AttachmentsApi.upload({ type: 'file', file })).id,
})

function fill(fields: TaskDraftFields) {
  initialData.value = fields as Partial<TaskFormSubmitData>
  teaching.value = { ...(fields.teaching ?? {}) }
  teachingOwn.value = !!fields.teachingOwn
  const files = fields.attachmentFiles ?? []
  uploads.reset(files.map(({ id, name, size }) => ({ id, name, size })))
  // 这些是装进来的起点，不是人改的。
  draft.baseline({
    teaching: teaching.value,
    teachingOwn: teachingOwn.value,
    attachmentIds: files.map((f) => f.id),
    attachmentFiles: files,
  })
  formKey.value++
}

/** 打开地址栏里的那份草稿；没有就是一份还没存的空白草稿。 */
async function openDraft(id: number | null) {
  draftReady.value = false
  publishedHere.value = false
  await draft.flush()
  let row: TaskDraft | null = null
  if (id !== null) {
    try {
      row = await TaskDraftsApi.read(id)
    } catch (error) {
      void errorHandler.handle(error as Error, { defaultMessage: t('spaces.detail.publishTask.draftLoadFailed') })
    }
  }
  // 改已发布题目的草稿在改题页上写。
  if (row?.taskId) {
    await router.replace({ name: 'TasksEdit', params: { spaceId: spaceId.value, taskId: row.taskId } })
    return
  }
  draft.open(row)
  fill(row?.fields ?? {})
  draftReady.value = true
  await agent.assistant.reload()
}

function useTemplate(index: number) {
  const template = templates.value[index]
  if (!template) return
  const fields = {
    name: template.title,
    description: template.content,
    submitterType: template.submitterType ?? undefined,
    rank: template.rank ?? undefined,
    minTeamSize: template.minTeamSize,
    maxTeamSize: template.maxTeamSize,
    defaultDeadline: template.defaultDeadline ?? undefined,
    requireRealName: template.requireRealName ?? undefined,
  }
  // 模板没写的几格保持原样。
  const given = Object.fromEntries(Object.entries(fields).filter(([, value]) => value !== undefined))
  void view.value?.form.apply(given as TaskDraftFields)
}

watch([teaching, teachingOwn], () => draft.changed({ teaching: teaching.value, teachingOwn: teachingOwn.value }), {
  deep: true,
})
watch(
  () => uploads.files.value,
  (files) =>
    draft.changed({
      attachmentIds: files.map((f) => f.id),
      attachmentFiles: files.map(({ id, name, size }) => ({ id, name, size })),
    }),
  { deep: true }
)

// --- 芝士 --------------------------------------------------------------------------

const view = ref<InstanceType<typeof PublishTaskView> | null>(null)
watch(view, (v) => draft.bind(v ? v.form : null))

const agent = useDraftAgent({
  draft,
  terms,
  home: () =>
    draft.draft.value
      ? { kind: 'draft', id: draft.draft.value.id, conversationId: draft.draft.value.conversationId }
      : null,
  draftsChanged: () => void refreshDrafts(),
  publishedHere: () => (publishedHere.value = true),
})
const { assistant } = agent

const host = computed(() => (view.value?.host as unknown as HTMLElement | null) ?? null)
const layer = useAgentPointer({
  host,
  writing: draft.writing,
  marks: draft.marks,
  active: computed(() => assistant.busy.value),
  descriptionEnd: () => view.value?.descriptionEnd() ?? null,
})

const agentOpen = ref(false)

// --- 发布 --------------------------------------------------------------------------

const publishing = ref(false)

// 能当导师的人：出题人自己和空间管理员（#3830）；新发的题默认是出题人自己。
const mentorChoices = computed(() => {
  const handle = myHandle()
  const me = handle ? { handle, name: myAccount()?.nickname || handle } : null
  return mentorOptions(spaceStore.currentSpace?.admins, me)
})

async function publish(form: TaskFormSubmitData) {
  if (publishing.value) return
  // 材料还在上传就先别发：附件 id 是发题那条请求的一部分，这时发出去会静默少一份。
  if (uploads.uploading.value) {
    toast.error(t('spaces.detail.publishTask.attachmentsUploading'))
    return
  }
  publishing.value = true
  try {
    await draft.flush()
    const row = await draft.ensure()
    // 表单校验过的样子就是要发的样子：先存进草稿，再照草稿发布。
    await TaskDraftsApi.save(
      row.id,
      draftFieldsOf({
        ...form,
        descriptionMarkdown: view.value?.form.values().descriptionMarkdown,
        teaching: teachingOwn.value && !isTeachingBlank(teaching.value) ? teaching.value : {},
        teachingOwn: teachingOwn.value,
        attachmentIds: uploads.ids.value,
      })
    )
    const {
      task: { approved },
    } = await TaskDraftsApi.publish(row.id)
    draft.gone()
    toast.success(
      approved
        ? t('spaces.detail.publishTask.createSuccess')
        : t('spaces.detail.publishTask.createSuccessAndWaitingAudit')
    )
    await router.replace(publishDoneRoute(spaceId.value))
  } catch (error) {
    void errorHandler.handle(error as Error, { defaultMessage: t('spaces.detail.publishTask.createFailed') })
  } finally {
    publishing.value = false
  }
}

function newDraft() {
  void router.push({ query: { ...route.query, draft: undefined } }).then(() => openDraft(null))
}

function openOther(id: number) {
  if (id !== draft.id.value) void router.push({ query: { ...route.query, draft: String(id) } })
}

function openTask(id: number) {
  void router.push({ name: 'TasksDetail', params: { spaceId: spaceId.value, taskId: id } })
}

onBeforeUnmount(() => void draft.flush())

watch(draftParam, (id) => {
  if (id !== draft.id.value) void openDraft(id)
})

watch(
  spaceId,
  () => {
    void refreshDrafts()
    void openDraft(draftParam.value)
  },
  { immediate: true }
)

const pointer = computed<AgentPointer | null>(() => layer.pointer.value)
const outline = computed<AgentBox | null>(() => layer.outline.value)
const marks = computed<AgentMark[]>(() => layer.marks.value)
const ready = computed(() => spaceReady.value && draftReady.value)
</script>

<template>
  <PublishTaskView
    ref="view"
    v-model:teaching="teaching"
    v-model:teaching-own="teachingOwn"
    v-model:agent-open="agentOpen"
    :ready="ready"
    :list-to="listTo"
    :form-key="formKey"
    :initial-data="initialData"
    :classification-topics="classificationTopics"
    :categories="activeCategories"
    :selected-category-id="preselectedCategoryId"
    :domain-groups="domainGroups"
    :templates="templates.map((template, index) => ({ index, name: template.name, description: template.description }))"
    :attachments="uploads.files.value"
    :uploading="uploads.uploading.value"
    :max-file-bytes="uploads.maxFileBytes.value"
    :materials="materials"
    :materials-state="materialsState"
    :library-to="spaceLibraryPath(spaceId)"
    :publishing="publishing"
    :mentor-options="mentorChoices"
    :default-mentor="myHandle() || undefined"
    :drafts="drafts"
    :current-draft-id="draft.id.value"
    :save-state="draft.saveState.value"
    :published-here="publishedHere"
    :pointer="pointer"
    :outline="outline"
    :marks="marks"
    :agent-name="t('work.agentAvatar.defaultName')"
    @cancel="router.push(listTo)"
    @use-template="useTemplate"
    @add-attachments="uploads.add"
    @remove-attachment="uploads.remove"
    @submit="publish"
    @change="draft.changed"
    @form-ready="draft.baseline"
    @open-draft="openOther"
    @new-draft="newDraft"
    @undo="agent.undoMark"
  >
    <template #agent>
      <AssistantPanel
        :personal-team-handle="myHandle()"
        :conversations="assistant.conversations.value"
        :current-id="assistant.current.value"
        :title="assistant.title.value"
        :messages="agent.panel.value.messages"
        :streaming="assistant.streaming.value"
        :tool="assistant.tool.value"
        :queued="assistant.queued.value"
        :notice="assistant.notice.value"
        :credit-refused="assistant.creditRefused.value"
        :busy="assistant.busy.value"
        :steps="agent.panel.value.steps"
        :changes="agent.panel.value.changes"
        :results="agent.panel.value.results"
        :files="agent.panel.value.files"
        context=""
        :greeting="t('tasks.assistant.draft.greeting')"
        :starters="[
          t('tasks.assistant.draft.starters.rubric'),
          t('tasks.assistant.draft.starters.harder'),
          t('tasks.assistant.draft.starters.check'),
        ]"
        :placeholder="t('tasks.assistant.draft.placeholder')"
        :hint="t('tasks.assistant.draft.hint')"
        can-attach
        :closable="agentOpen"
        @send="agent.ask"
        @stop="assistant.stop().catch(() => undefined)"
        @new="assistant.startNew"
        @select="assistant.select"
        @close="agentOpen = false"
        @undo="agent.undo"
        @undo-all="agent.undoAll"
        @jump="layer.jump"
        @open-draft="openOther"
        @open-task="openTask"
        @attach="agent.attach"
        @remove-file="agent.removeFile"
      />
    </template>
  </PublishTaskView>
</template>
