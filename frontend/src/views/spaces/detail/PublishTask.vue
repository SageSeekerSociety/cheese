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
import type { UploadProgress } from '@/api/partUpload'
import type { SpaceTeaching, TaskDraft, TaskDraftFields, TaskDraftSummary, TaskFormSubmitData } from '@/types'
import type { StripItem } from './TaskDraftStrip.vue'

import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'
import { toast } from 'vuetify-sonner'
import { storeToRefs } from 'pinia'

import { useAgentTypes } from '@/composables/useAgentTypes'
import { useAttachmentUploads } from '@/composables/useAttachmentUploads'
import { useDraftAgent } from '@/composables/useDraftAgent'
import { useSkillCatalog } from '@/composables/useSkillCatalog'
import { useSpaceData } from '@/composables/useSpaceData'
import { useSpaceMaterials } from '@/composables/useSpaceMaterials'
import { useStarterUpload } from '@/composables/useStarterUpload'
import { useTaskDraft } from '@/composables/useTaskDraft'

import PublishTaskView from './PublishTaskView.vue'

import { AttachmentsApi } from '@/api/attachments'
import { TaskDraftsApi } from '@/api/taskDrafts'
import { fieldLabel } from '@/lib/draftChangeView'
import { publishDoneRoute, spaceLibraryPath } from '@/lib/spaceRouteNames'
import { missingOf } from '@/lib/taskDraftAgent'
import { mentorOptions } from '@/lib/taskMentors'
import { isTeachingBlank } from '@/lib/teaching'
import { myAccount, myHandle } from '@/me'
import errorHandler from '@/services/ErrorHandler'
import { agentDocked, agentOpen, askAgent, attachToAgent, openAgent, useAgentContext } from '@/stores/agent'
import { useSpaceStore } from '@/stores/space'
import { draftFieldsOf } from '@/types'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()

const spaceStore = useSpaceStore()
const spaceData = useSpaceData()
const { currentSpaceId, templates, classificationTopics, categories, domainGroups } = storeToRefs(spaceStore)
// 「默认队友」那一格的候选预设：取数留在这里（画面那一半不碰 API 层）。
const { types: agentTypes, failed: agentTypesFailed } = useAgentTypes()
const { skills: skillCatalog, failed: skillCatalogFailed } = useSkillCatalog()

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

/** 芝士换算分类、标签、邮箱域名组和导师名字用的。 */
const terms = () => ({
  categories: activeCategories.value.map((c) => ({ id: c.id, name: c.name })),
  topics: classificationTopics.value.map((topic) => ({ id: topic.id, name: topic.name })),
  domainGroups: domainGroups.value.map((g) => ({ id: g.id, name: g.name })),
  mentors: mentorChoices.value.map((m) => ({ handle: m.value, name: m.title })),
  materials: materials.value.map(({ id, name }) => ({ id, name })),
  teammates: agentTypes.value.map(({ name, title }) => ({ name, title })),
  teaching: teaching.value,
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
    spaceStore.setMyDraftCount(drafts.value.length)
  } catch {
    // 列表读不出来不挡着写题：顶栏的「草稿」暂时是空的。
  }
}

const draftReady = ref(false)
const initialData = ref<Partial<TaskFormSubmitData>>({})
/** 换一份初值就重挂表单。 */
const formKey = ref(0)
const publishedHere = ref(false)
/** 上一次发布没成的原因，留在页头，直到再点发布或换一份草稿。 */
const publishError = ref('')

/** 不在表单里、但也存进草稿的几样：附件、AI 指导、默认队友。 */
const teaching = ref<SpaceTeaching>({})
const teachingOwn = ref(false)
/** 这道题指定的默认队友（#944）：`''` = 不指定（继承项目集 / 空间）。新题没有自己的可清，
 *  所以草稿里存的是预设名或 `null`。 */
const defaultRole = ref('')
/** 传成游离的附件（`POST /attachments`），浏览器报得出传了多少，这一格写百分比。 */
async function uploadLoose(file: File, progress: UploadProgress): Promise<number> {
  const { id } = await AttachmentsApi.upload({ type: 'file', file }, (event) =>
    progress(event.loaded, event.total ?? file.size)
  )
  return id
}
const uploads = useAttachmentUploads({ upload: uploadLoose })
/** 起始代码：选完就交给服务端，先查能不能作为起始代码（和放进题目时同一个检查），合格
 *  才存成附件；不合格的这里就说原因、什么都不存，发布前就知道。合格的随草稿存着、随草稿
 *  发布，和芝士替人写进草稿的那一份走同一条路。 */
const starter = useStarterUpload<number>({
  upload: async (file, progress) => {
    const { id } = await TaskDraftsApi.uploadStarter(file, (event) => progress(event.loaded, event.total ?? file.size))
    return { id, name: file.name, size: file.size }
  },
})

function fill(fields: TaskDraftFields) {
  initialData.value = fields as Partial<TaskFormSubmitData>
  teaching.value = { ...(fields.teaching ?? {}) }
  teachingOwn.value = !!fields.teachingOwn
  defaultRole.value = fields.defaultRole ?? ''
  const files = fields.attachmentFiles ?? []
  uploads.reset(files.map(({ id, name, size }) => ({ id, name, size })))
  starter.reset(fields.starterFile ?? null)
  // 这些是装进来的起点，不是人改的。
  draft.baseline({
    teaching: teaching.value,
    teachingOwn: teachingOwn.value,
    defaultRole: defaultRole.value || null,
    attachmentIds: files.map((f) => f.id),
    attachmentFiles: files,
    starterAttachmentId: fields.starterFile?.id ?? null,
    starterFile: fields.starterFile ?? null,
  })
  formKey.value++
}

/** 打开地址栏里的那份草稿；没有就是一份还没存的空白草稿。 */
async function openDraft(id: number | null) {
  draftReady.value = false
  publishedHere.value = false
  publishError.value = ''
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
  live.value = { ...(row?.fields ?? {}) }
  fill(row?.fields ?? {})
  draftReady.value = true
  // 全部发布时这道没发出去：切过来就把缺的格子标出来。
  if (flagged.value && row && missingOf(row.fields ?? {}).length) {
    await nextTick()
    view.value?.check()
  }
}

/** 「从文件导入」：打开芝士，把文件交给它，不带话；文件在这一页是导入的意思，芝士知道。 */
async function importFiles(files: File[]) {
  openAgent()
  await attachToAgent(files)
  await askAgent('')
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
watch(defaultRole, (role) => draft.changed({ defaultRole: role || null }))
watch(
  () => uploads.files.value,
  (files) =>
    draft.changed({
      attachmentIds: files.map((f) => f.id),
      attachmentFiles: files.map(({ id, name, size }) => ({ id, name, size })),
    }),
  { deep: true }
)

watch(starter.file, (file) => draft.changed({ starterAttachmentId: file?.id ?? null, starterFile: file }))

// --- 芝士 --------------------------------------------------------------------------

const view = ref<InstanceType<typeof PublishTaskView> | null>(null)
watch(view, (v) =>
  draft.bind(
    v
      ? {
          values: () => v.form.values(),
          apply: async (fields: TaskDraftFields) => ({ ...pageApply(fields), ...(await v.form.apply(fields)) }),
        }
      : null
  )
)

/** 草稿里不在表单上的几格（AI 指导、默认队友、附件），芝士写到它们时由页面自己填。 */
function pageApply(fields: TaskDraftFields): TaskDraftFields {
  const shown: TaskDraftFields = {}
  if ('teaching' in fields) teaching.value = { ...(fields.teaching ?? {}) }
  if ('teachingOwn' in fields) teachingOwn.value = !!fields.teachingOwn
  if ('defaultRole' in fields) defaultRole.value = fields.defaultRole ?? ''
  if ('attachmentFiles' in fields) {
    const files = fields.attachmentFiles ?? []
    uploads.reset(files.map(({ id, name, size }) => ({ id, name, size })))
    shown.attachmentFiles = files
    shown.attachmentIds = files.map((f) => f.id)
  }
  if ('starterFile' in fields) {
    starter.reset(fields.starterFile ?? null)
    shown.starterFile = fields.starterFile ?? null
    shown.starterAttachmentId = fields.starterFile?.id ?? null
  }
  return shown
}

const agent = useDraftAgent({
  draft,
  host: () => (view.value?.host as unknown as HTMLElement | null) ?? null,
  terms,
  draftsChanged: () => void refreshDrafts(),
  openDraft: (id) => openOther(id),
  openTask: (id) => openTask(id),
  publishedHere: () => (publishedHere.value = true),
  ordinal: (id) => {
    const at = stripIds.value.indexOf(id)
    return at >= 0 && stripIds.value.length > 1 ? at + 1 : undefined
  },
})
// 芝士读到的是这份草稿；面板在发布页给出题的问候和开场问题，能交文件（从文件导入）。
useAgentContext(() => ({
  page: draft.id.value ? { kind: 'draft', id: draft.id.value } : null,
  label: t('tasks.assistant.page.publish'),
  greeting: t('tasks.assistant.draft.greeting'),
  starters: [
    t('tasks.assistant.draft.starters.rubric'),
    t('tasks.assistant.draft.starters.harder'),
    t('tasks.assistant.draft.starters.check'),
  ],
  placeholder: t('tasks.assistant.draft.placeholder'),
  hint: t('tasks.assistant.draft.hint'),
  attachAsMaterials: true,
}))

const { mdAndUp } = useDisplay()
// 桌面上进这一页时，芝士停在右边就把它打开：表单和面板并排，芝士写进来看得见。
onMounted(() => {
  if (mdAndUp.value && agentDocked.value) openAgent()
})

// --- 一份文件拆出的几道题 ------------------------------------------------------------
//
// 芝士在同一段对话里建的几份新题目草稿排在表单上方（`TaskDraftStrip`），点了切换；末尾的
// 「全部发布」把齐了的几道一起发出去，缺项的那道标出来、留在原处。

/** 眼前这份表单现在的样子（人改的、芝士写的都算），算「还缺什么」用。 */
const live = ref<TaskDraftFields>({})
function onFormChange(fields: TaskDraftFields) {
  live.value = { ...live.value, ...fields }
  draft.changed(fields)
}
function onFormReady(fields: TaskDraftFields) {
  live.value = { ...fields }
  draft.baseline(fields)
}

/** 「默认队友」沿用的那一位：表单上选中的项目集设的；还没选项目集时不知道。 */
const inheritedRole = computed(() => {
  const id = live.value.categoryId ?? preselectedCategoryId.value
  const category = activeCategories.value.find((c) => c.id === id)
  return category ? category.defaultRole ?? null : undefined
})

/** 这一组里已经发出去的（发布后草稿就没了，这一排还留着它，标「已发布」）。 */
const publishedInGroup = ref<{ id: number; name: string; conversationId: string }[]>([])
const flagged = ref(false)
const stripNote = ref<{ text: string; ok: boolean } | null>(null)
const publishingAll = ref(false)

const conversationHere = computed(() => {
  const here = draft.draft.value
  return here && !here.taskId ? here.conversationId : null
})
const group = computed(() =>
  conversationHere.value ? drafts.value.filter((d) => !d.taskId && d.conversationId === conversationHere.value) : []
)
watch(conversationHere, () => {
  publishedInGroup.value = []
  flagged.value = false
  stripNote.value = null
})

const stripItems = computed<StripItem[]>(() => {
  const here = draft.id.value
  const target = agent.writingTarget.value
  const done = new Set(publishedInGroup.value.map((d) => d.id))
  const rows: StripItem[] = group.value
    .filter((d) => !done.has(d.id))
    .map((d) => ({
      id: d.id,
      name: d.id === here ? live.value.name ?? d.name : d.name,
      writing: !!target && target.draft === d.id,
      missing: (d.id === here ? missingOf(live.value) : d.missing).map((key) => fieldLabel(key)),
      published: false,
    }))
  for (const done of publishedInGroup.value)
    if (done.conversationId === conversationHere.value)
      rows.push({ id: done.id, name: done.name, writing: false, missing: [], published: true })
  rows.sort((a, b) => (a.id ?? 0) - (b.id ?? 0))
  // 芝士正在建的那份：服务端还没给 id，先占一格。
  if (target && target.draft === null && conversationHere.value)
    rows.push({ id: null, name: target.name, writing: true, missing: [], published: false })
  return rows
})
const stripIds = computed(() => stripItems.value.map((item) => item.id).filter((id): id is number => id !== null))

const strip = computed(() =>
  stripItems.value.length > 1
    ? {
        items: stripItems.value,
        source: draft.draft.value?.source || group.value.find((d) => d.source)?.source || '',
        flagged: flagged.value,
        publishing: publishingAll.value,
        note: stripNote.value,
      }
    : null
)

async function removeDraft(id: number) {
  try {
    await TaskDraftsApi.remove(id)
  } catch (error) {
    void errorHandler.handle(error as Error, { defaultMessage: t('spaces.detail.publishTask.strip.removeFailed') })
    return
  }
  const next = stripIds.value.find((other) => other !== id && !publishedInGroup.value.some((d) => d.id === other))
  await refreshDrafts()
  if (id !== draft.id.value) return
  draft.gone()
  if (next !== undefined) openOther(next)
  else newDraft()
}

/** 全部发布：每道各自看齐没齐，齐的发出去，缺项的标出来、留在原处。 */
async function publishAll() {
  if (publishingAll.value || publishing.value) return
  if (uploads.uploading.value || starter.uploading.value) {
    toast.error(t('spaces.detail.publishTask.attachmentsUploading'))
    return
  }
  flagged.value = true
  publishingAll.value = true
  stripNote.value = null
  const items = stripItems.value.filter((item) => item.id !== null && !item.published)
  const sentences: string[] = []
  let sent = 0
  try {
    await draft.flush()
    for (const item of items) {
      const name = item.name || t('spaces.detail.publishTask.strip.untitled')
      if (item.missing.length) {
        sentences.push(
          t('spaces.detail.publishTask.strip.lacks', {
            name,
            fields: item.missing.join(t('spaces.detail.publishTask.strip.joiner')),
          })
        )
        continue
      }
      try {
        await TaskDraftsApi.publish(item.id as number)
        sent += 1
        publishedInGroup.value = [
          ...publishedInGroup.value,
          { id: item.id as number, name, conversationId: conversationHere.value ?? '' },
        ]
        if (item.id === draft.id.value) draft.gone()
      } catch {
        sentences.push(t('spaces.detail.publishTask.strip.failed', { name }))
      }
    }
  } finally {
    publishingAll.value = false
    await refreshDrafts()
  }
  if (!sentences.length) {
    toast.success(t('spaces.detail.publishTask.strip.publishedSome', { n: sent }))
    await router.replace(publishDoneRoute(spaceId.value))
    return
  }
  if (sent) sentences.unshift(t('spaces.detail.publishTask.strip.publishedSome', { n: sent }))
  stripNote.value = { text: sentences.join(t('spaces.detail.publishTask.strip.separator')), ok: false }
  const left = stripItems.value.find((item) => item.id !== null && !item.published)
  if (draft.published.value && left?.id) openOther(left.id)
  else view.value?.check()
}

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
  if (uploads.uploading.value || starter.uploading.value) {
    toast.error(t('spaces.detail.publishTask.attachmentsUploading'))
    return
  }
  publishing.value = true
  publishError.value = ''
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
        defaultRole: defaultRole.value || null,
        attachmentIds: uploads.ids.value,
        starterAttachmentId: starter.file.value?.id ?? null,
        starterFile: starter.file.value,
      })
    )
    const { task } = await TaskDraftsApi.publish(row.id)
    const { approved } = task
    draft.gone()
    toast.success(
      approved
        ? t('spaces.detail.publishTask.createSuccess')
        : t('spaces.detail.publishTask.createSuccessAndWaitingAudit')
    )
    await router.replace(publishDoneRoute(spaceId.value))
  } catch (error) {
    // 页头留着这句，不再另弹一条转眼就消失的提示。
    publishError.value = errorHandler.sentence(error as Error, t('spaces.detail.publishTask.createFailed'))
    void errorHandler.handle(error as Error, { showToast: false })
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

const ready = computed(() => spaceReady.value && draftReady.value)
</script>

<template>
  <PublishTaskView
    ref="view"
    v-model:teaching="teaching"
    v-model:teaching-own="teachingOwn"
    v-model:default-role="defaultRole"
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
    :upload-percent="uploads.percent.value"
    :max-file-bytes="uploads.maxFileBytes.value"
    :starter="starter.file.value"
    :starter-uploading="starter.uploading.value"
    :starter-percent="starter.percent.value"
    :materials="materials"
    :materials-state="materialsState"
    :library-to="spaceLibraryPath(spaceId)"
    :publishing="publishing"
    :publish-error="publishError"
    :agent-types="agentTypes"
    :agent-types-failed="agentTypesFailed"
    :skill-catalog="skillCatalog"
    :skill-catalog-failed="skillCatalogFailed"
    :inherited-role="inheritedRole"
    :mentor-options="mentorChoices"
    :default-mentor="myHandle() || undefined"
    :drafts="drafts"
    :current-draft-id="draft.id.value"
    :save-state="draft.saveState.value"
    :published-here="publishedHere"
    :agent-writing="draft.writing.value"
    :space-teaching="spaceStore.currentSpace?.teaching ?? null"
    :strip="strip"
    :agent-name="t('work.agentAvatar.defaultName')"
    @cancel="router.push(listTo)"
    @use-template="useTemplate"
    @import-files="importFiles"
    @add-attachments="uploads.add"
    @remove-attachment="uploads.remove"
    @pick-starter="starter.pick"
    @remove-starter="starter.remove"
    @submit="publish"
    @change="onFormChange"
    @form-ready="onFormReady"
    @open-draft="openOther"
    @new-draft="newDraft"
    @remove-draft="removeDraft"
    @publish-all="publishAll"
  >
  </PublishTaskView>
</template>
