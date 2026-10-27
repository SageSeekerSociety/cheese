<script setup lang="ts">
// 改题页：和发题页同一张表（`TaskForm`），参与方式改不了，附件直接传到这道题上、从这道
// 题上摘，AI 指导和发题时一样单独写或沿用空间的默认。没有导入和模板。
//
// 改动先存成这道题的修改草稿（`useTaskDraft`）：边改边存，离开再回来还在，点「保存更改」
// 才落到题目上，学生在那之前看到的是原来的题目。芝士停在右边，接着的是题目页「问芝士」
// 的那段对话；它改的也是这份草稿，表单跟着动（`useDraftAgent`）。
import type { SpaceTeaching, TaskDraftFields, TaskFormSubmitData, TaskStage } from '@/types'

import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
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

import { useStageChangeCheck } from './composables/useStageChangeCheck'
import { useTaskData } from './composables'

import { ApiError } from '@/api/http'
import { TaskBoardApi } from '@/api/taskBoard'
import { TaskDraftsApi } from '@/api/taskDrafts'
import {
  addTaskFile,
  listTaskFiles,
  removeTaskFile,
  removeTaskStarter,
  replaceTaskFile,
  setTaskStarter,
} from '@/api/taskFiles'
import AgentEntryButton from '@/components/assistant/AgentEntryButton.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import DefaultTeammatePicker from '@/components/common/DefaultTeammatePicker.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import TeachingFields from '@/components/common/TeachingFields.vue'
import TaskAttachmentPicker from '@/components/tasks/TaskAttachmentPicker.vue'
import TaskForm from '@/components/tasks/TaskForm.vue'
import TaskStageChangeConfirm from '@/components/tasks/TaskStageChangeConfirm.vue'
import TaskStarterPicker from '@/components/tasks/TaskStarterPicker.vue'
import { closeOverlay } from '@/lib/backOut'
import { spaceLibraryPath } from '@/lib/spaceRouteNames'
import { mentorOptions } from '@/lib/taskMentors'
import { defaultRolePayload, isTeachingBlank } from '@/lib/teaching'
import errorHandler from '@/services/ErrorHandler'
import { agentDocked, agentOpen, openAgent, useAgentContext } from '@/stores/agent'
import { useSpaceStore } from '@/stores/space'
import { draftFieldsOf } from '@/types'

const { t } = useI18n()
const { mdAndUp } = useDisplay()

const router = useRouter()
const route = useRoute()
const taskId = Number(route.params.taskId)

const { taskData, loading, error, loadTaskData } = useTaskData()

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

const { materials, state: materialsState } = useSpaceMaterials(spaceId)
const { skills: skillCatalog, failed: skillCatalogFailed } = useSkillCatalog()
const { types: agentTypes, failed: agentTypesFailed } = useAgentTypes()

/** 附件直接放进这道题的资料库、在那里替换和删掉：改题页上的增删换当场生效，不等保存，
 *  每一次都让题目记一版。认的是它在资料库里的名字。 */
const uploads = useAttachmentUploads<string>({
  upload: async (file, progress) => (await addTaskFile(taskId, file, progress)).name,
  remove: async (name) => {
    await removeTaskFile(taskId, name)
  },
  replace: async (name, file, progress) => {
    await replaceTaskFile(taskId, name, file, progress)
  },
})

/** 起始代码同样当场生效：换一份、删掉都让题目记一版，之后领题的人拿到新的那份。 */
const starter = useStarterUpload<string>({
  upload: async (file, progress) => {
    const placed = await setTaskStarter(taskId, file, progress)
    return { id: placed.name, name: placed.name, size: placed.size }
  },
  remove: async () => {
    await removeTaskStarter(taskId)
  },
})

// 能当导师的人：出题人和空间管理员（#3830）。
const mentorChoices = computed(() =>
  mentorOptions(
    taskData.value?.space?.admins,
    taskData.value?.creator ? { handle: taskData.value.creator.username, name: taskData.value.creator.nickname } : null
  )
)

const teaching = ref<SpaceTeaching>({})
const teachingOwn = ref(false)
/** 这道题指定的默认队友（#944）：`''` = 这一层不单独指定。`raw` 是这道题原始的覆盖值，
 *  草稿里存的是保存时要交的那一格（`defaultRolePayload`；`null` = 不动）。 */
const defaultRole = ref('')
const defaultRoleRaw = ref<string | null>(null)

/** 题目和空间的辅导设置同时生效：这道题写了什么，就算「本题有自己的」。 */
function setTeaching(value: SpaceTeaching) {
  teaching.value = value
  teachingOwn.value = !isTeachingBlank(value)
}

// --- 修改草稿 ------------------------------------------------------------------------

const draft = useTaskDraft({ spaceId: () => spaceId.value })
const initialData = ref<Partial<TaskFormSubmitData>>({})
const formKey = ref(0)
const draftReady = ref(false)

async function openChanges() {
  draftReady.value = false
  const row = await TaskDraftsApi.forTask(taskId)
  draft.open(row)
  initialData.value = row.fields as Partial<TaskFormSubmitData>
  teaching.value = { ...(row.fields.teaching ?? {}) }
  teachingOwn.value = !!row.fields.teachingOwn && !isTeachingBlank(teaching.value)
  // `null` 是「不动」：这一格就是这道题原来的样子。
  defaultRole.value = row.fields.defaultRole ?? defaultRoleRaw.value ?? ''
  draft.baseline({ teaching: teaching.value, teachingOwn: teachingOwn.value, defaultRole: rolePayload() })
  formKey.value++
  draftReady.value = true
}

/** 有没有还没保存到题目上的修改。 */
const unsaved = computed(() => (draft.draft.value?.version ?? 0) > 0)

watch([teaching, teachingOwn], () => draft.changed({ teaching: teaching.value, teachingOwn: teachingOwn.value }), {
  deep: true,
})
/** 默认队友在草稿里的那一格：本来没写、又选「继承」→ `null`（不动项目集那份）；本来写过、
 *  又选回「继承」→ `''`，明确清掉这道题的覆盖。 */
function rolePayload(): string | null {
  return defaultRolePayload(defaultRole.value, defaultRoleRaw.value) ?? null
}
watch(defaultRole, () => draft.changed({ defaultRole: rolePayload() }))

type FormHandle = InstanceType<typeof TaskForm>
const form = ref<FormHandle | null>(null)
const host = ref<HTMLElement | null>(null)
watch(form, (f) =>
  draft.bind(
    f
      ? {
          values: () => f.values(),
          apply: async (fields: TaskDraftFields) => {
            // 不在表单上的 AI 指导由页面自己填。
            if ('teaching' in fields) teaching.value = { ...(fields.teaching ?? {}) }
            if ('teachingOwn' in fields) teachingOwn.value = !!fields.teachingOwn
            if ('defaultRole' in fields) defaultRole.value = fields.defaultRole ?? defaultRoleRaw.value ?? ''
            return f.apply(fields)
          },
        }
      : null
  )
)

/** 表单上现在显示的项目集：「默认队友」沿用的是它设的那一位。从表单挂好那一刻起就读它
 *  （它的起点可能是题目本身，也可能是打开的修改草稿），之后人改的、芝士写的都经 `change`。 */
const formCategoryId = ref<number | undefined>(undefined)
function onFormReady(fields: TaskDraftFields) {
  formCategoryId.value = fields.categoryId
  draft.baseline(fields)
}
function onFormChange(fields: TaskDraftFields) {
  if ('categoryId' in fields) formCategoryId.value = fields.categoryId
  draft.changed(fields)
}
const inheritedRole = computed(() => {
  const category = editCategories.value.find((c) => c.id === formCategoryId.value)
  return category ? category.defaultRole ?? null : undefined
})

// --- 芝士 -----------------------------------------------------------------------------

const terms = () => ({
  categories: editCategories.value.map((c) => ({ id: c.id, name: c.name })),
  topics: (taskData.value?.space?.classificationTopics ?? []).map((topic) => ({ id: topic.id, name: topic.name })),
  domainGroups: domainGroups.value.map((g) => ({ id: g.id, name: g.name })),
  mentors: mentorChoices.value.map((m) => ({ handle: m.value, name: m.title })),
  materials: materials.value.map(({ id, name }) => ({ id, name })),
  teammates: agentTypes.value.map(({ name, title }) => ({ name, title })),
  teaching: teaching.value,
})

const agent = useDraftAgent({
  draft,
  host: () => host.value,
  terms,
  publishedHere: () => navigateToDetail(),
  openTask: (id) => openTask(id),
})
const { assistant } = agent
// 芝士读到的是这份修改草稿；面板在这一页给改题的开场问题，能交文件。
useAgentContext(() => ({
  page: draft.id.value ? { kind: 'draft', id: draft.id.value } : null,
  label: t('tasks.assistant.page.publish'),
  starters: [
    t('tasks.assistant.draft.editStarters.later'),
    t('tasks.assistant.draft.editStarters.rubric'),
    t('tasks.assistant.draft.editStarters.check'),
  ],
  placeholder: t('tasks.assistant.draft.placeholder'),
  hint: t('tasks.assistant.draft.hint'),
  attachAsMaterials: true,
}))

// 桌面上进这一页时，芝士停在右边就把它打开：表单和面板并排，芝士写进来看得见。
onMounted(() => {
  if (mdAndUp.value && agentDocked.value) openAgent()
})

// --- 保存 ---------------------------------------------------------------------------

const formInvalid = ref(0)
const attempted = ref(false)
const saving = ref(false)
const resubmit = ref(false)
/** 上一次保存没成的原因，留在页头，直到再点保存（同发题页的发布）。 */
const saveError = ref('')
// 原因属于它说的那份修改：换了一份（放弃修改后重开的、别处换掉的），就不再是它的事。
watch(
  () => draft.id.value,
  () => (saveError.value = '')
)

// 改了阶段的截止、增删了阶段，存下去可能让学生被判未通过或回到进行中：存之前先说有几个人。
const stageCheck = useStageChangeCheck((stages) => TaskBoardApi.stageEffect(taskId, stages))

/** 点阶段的 ✕ 时就问服务端：删掉它之后的阶段存不存得下（有人交过的阶段删不掉），
 *  存不下就交回那句拒绝。问的是保存前预览那条路（只算不写），拒绝和保存时同一句。 */
async function stageRemovalRefusal(stages: TaskStage[]): Promise<string | null> {
  try {
    await TaskBoardApi.stageEffect(taskId, stages)
    return null
  } catch (err) {
    // Only the save's own refusal (400) stops the removal; a request that failed
    // for another reason leaves it to the confirm and the save.
    return err instanceof ApiError && err.status === 400 ? err.message : null
  }
}

/** 审核没通过的题，改完可以一并重新提交审核。 */
const rejected = computed(() => taskData.value?.approved === 'DISAPPROVED')

function save(again: boolean) {
  attempted.value = true
  resubmit.value = again
  form.value?.submit()
}

async function onSubmit(data: TaskFormSubmitData) {
  if (saving.value) return
  saving.value = true
  saveError.value = ''
  try {
    if (data.stages && !(await stageCheck.allows(data.stages))) return
    await draft.flush()
    const row = await draft.ensure()
    // 表单校验过的样子就是要保存的样子：先存进修改草稿，再把草稿保存到题目上。
    await TaskDraftsApi.save(
      row.id,
      draftFieldsOf({
        ...data,
        descriptionMarkdown: form.value?.values().descriptionMarkdown,
        // 整份替换：沿用默认就交一份空的，让空间（或项目集）那一层重新生效。
        teaching: teachingOwn.value && !isTeachingBlank(teaching.value) ? teaching.value : {},
        teachingOwn: teachingOwn.value,
        defaultRole: rolePayload(),
      })
    )
    await TaskDraftsApi.publish(row.id)
    draft.gone()
    if (resubmit.value) {
      await TaskBoardApi.resubmitTask(taskId)
      toast.success(t('tasks.edit.resubmitted'))
    } else {
      toast.success(t('tasks.detail.updateSuccess'))
    }
    navigateToDetail()
  } catch (err) {
    // 页头留着这句，不再另弹一条转眼就消失的提示。
    saveError.value = errorHandler.sentence(err as Error, t('global.updateFailed'))
    void errorHandler.handle(err as Error, { showToast: false })
  } finally {
    saving.value = false
  }
}

/** 放弃修改：删掉这份草稿，表单回到题目现在的样子。 */
async function discard() {
  const id = draft.id.value
  if (!id) return
  try {
    draft.gone()
    await TaskDraftsApi.remove(id)
    await openChanges()
  } catch (err) {
    void errorHandler.handle(err as Error, { defaultMessage: t('global.updateFailed') })
  }
}

// 保存 / 取消之后回题目详情。**不是 push**：进来时就是从详情 push 过来的，出去再 push
// 一次，身后就多一条详情，按 ← 会落回那张刚保存过的表单。去向是定的（详情），所以走
// closeOverlay：身后正是它就退一格，否则 replace 过去。
const navigateToDetail = () => closeOverlay(router, detailTo.value)

function openTask(id: number) {
  void router.push({ name: 'TasksDetail', params: { spaceId: spaceId.value, taskId: id } })
}

onMounted(async () => {
  await loadTaskData()
  const task = taskData.value
  if (!task) return
  teaching.value = { ...(task.teaching ?? {}) }
  teachingOwn.value = !isTeachingBlank(teaching.value)
  // 这道题自己写的默认队友（原始覆盖值）；没写过就是 `null` → 选「继承」。
  defaultRoleRaw.value = task.defaultRole ?? null
  defaultRole.value = task.defaultRole ?? ''
  if (task.space?.id) {
    spaceStore.setCurrentSpaceId(task.space.id)
    await Promise.all([spaceData.fetchDomainGroups(), spaceData.fetchCategories()])
  }
  try {
    await openChanges()
  } catch (err) {
    void errorHandler.handle(err as Error, { defaultMessage: t('tasks.edit.loadFailed') })
  }
  void assistant.load()
  try {
    const data = await listTaskFiles(taskId)
    uploads.reset(data.files.map(({ name, size }) => ({ id: name, name, size })))
    starter.reset(data.starter ? { id: data.starter.name, name: data.starter.name, size: data.starter.size } : null)
  } catch {
    // 读不出来就当没有：增删仍然走得通，只是这一页不列已有的那几份。
  }
})

onBeforeUnmount(() => void draft.flush())
</script>

<template>
  <PageHeader show-on-mobile>
    <nav class="te__crumb">
      <router-link :to="listTo" class="te__crumb-link">{{ t('spaces.detail.allContests') }}</router-link>
      <v-icon size="16" class="te__crumb-sep">mdi-chevron-right</v-icon>
      <router-link :to="detailTo" class="te__crumb-link" data-user-content>{{ taskData?.name ?? '' }}</router-link>
      <v-icon size="16" class="te__crumb-sep">mdi-chevron-right</v-icon>
      <span class="te__crumb-here">{{ t('tasks.edit.crumb') }}</span>
    </nav>
    <template #actions>
      <span v-if="attempted && formInvalid" class="te__blocking">{{
        t('spaces.detail.publishTask.blocking', { n: formInvalid })
      }}</span>
      <span v-else-if="saveError" class="te__blocking" role="alert" data-testid="edit-save-failed">{{
        saveError
      }}</span>
      <span v-else-if="unsaved" class="te__unsaved" data-testid="edit-unsaved">{{ t('tasks.edit.unsaved') }}</span>
      <BaseButton v-if="unsaved" kind="ghost" :disabled="saving" @click="discard">{{
        t('tasks.edit.discard')
      }}</BaseButton>
      <AgentEntryButton
        v-if="mdAndUp && !agentOpen"
        :name="t('work.agentAvatar.defaultName')"
        data-testid="edit-agent-open"
        @click="openAgent"
      />
      <BaseButton kind="ghost" :disabled="saving" @click="navigateToDetail">{{ t('global.cancel') }}</BaseButton>
      <BaseButton
        v-if="rejected"
        kind="secondary"
        :loading="saving && resubmit"
        :disabled="saving || uploads.uploading.value"
        @click="save(true)"
        >{{ t('tasks.edit.saveAndResubmit') }}</BaseButton
      >
      <BaseButton
        kind="primary"
        :loading="saving && !resubmit"
        :disabled="saving || uploads.uploading.value || !taskData"
        @click="save(false)"
        >{{ t('tasks.edit.saveChanges') }}</BaseButton
      >
    </template>
  </PageHeader>

  <div ref="host" class="te">
    <div v-if="loading" class="py-12 text-center">
      <v-progress-circular indeterminate color="primary" />
    </div>
    <!-- A failed read trades the form for the error (docs/design-system.md §3.10). -->
    <BaseLoadError v-else-if="error" :title="t('tasks.loadError.title')" :error="error" @retry="loadTaskData" />
    <TaskForm
      v-else-if="taskData && draftReady"
      ref="form"
      :key="formKey"
      :initial-data="initialData"
      is-editing
      :start-mode-locked="(taskData.participants?.live ?? 0) > 0"
      :classification-topics="taskData.space?.classificationTopics || []"
      :categories="editCategories"
      :domain-groups="domainGroups"
      :mentor-options="mentorChoices"
      :agent-writing="draft.writing.value"
      :stage-removal-refusal="stageRemovalRefusal"
      @invalid="formInvalid = $event"
      @submit="onSubmit"
      @change="onFormChange"
      @ready="onFormReady"
    >
      <template #attachments>
        <div class="te__files">
          <TaskAttachmentPicker
            :files="uploads.files.value"
            :uploading="uploads.uploading.value"
            :percent="uploads.percent.value"
            :max-file-bytes="uploads.maxFileBytes.value"
            replaceable
            @add="uploads.add"
            @remove="uploads.remove(String($event))"
            @replace="(id, file) => uploads.replace(String(id), file)"
          />
          <TaskStarterPicker
            :file="starter.file.value"
            :uploading="starter.uploading.value"
            :percent="starter.percent.value"
            confirm-remove
            @pick="starter.pick"
            @remove="starter.remove"
          />
        </div>
      </template>
      <template #teaching>
        <div class="te__teaching" data-agent-field="ai_guidance">
          <DefaultTeammatePicker
            v-model="defaultRole"
            :raw-value="defaultRoleRaw"
            :types="agentTypes"
            :types-failed="agentTypesFailed"
            :inherited="inheritedRole"
          />
          <TeachingFields
            :model-value="teaching"
            inherit
            :space-teaching="spaceStore.currentSpace?.teaching ?? null"
            :materials="materials"
            :materials-state="materialsState"
            :library-to="spaceLibraryPath(spaceId)"
            :skill-catalog="skillCatalog"
            :skill-catalog-failed="skillCatalogFailed"
            @update:model-value="setTeaching"
          />
        </div>
      </template>
    </TaskForm>
    <TaskStageChangeConfirm :effect="stageCheck.effect.value" @answer="stageCheck.answer" />
  </div>
</template>

<style scoped>
.te {
  position: relative;
  width: min(var(--page-w), 100%);
  margin: 0 auto;
  padding: 8px 16px 64px;
}

.te__crumb {
  display: flex;
  flex: 0 1 auto;
  gap: 4px;
  align-items: center;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  font-size: 14px;
  line-height: var(--lh-14);
}

.te__crumb-link {
  overflow: hidden;
  color: var(--muted);
  text-decoration: none;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.te__crumb-link:hover {
  color: var(--ink);
}

.te__crumb-sep {
  flex: none;
  color: var(--faint);
}

.te__crumb-here {
  flex: none;
  color: var(--ink);
  font-weight: 600;
}

.te__blocking {
  color: var(--danger-ink);
  font-size: 12px;
  line-height: var(--lh-12);
}

.te__files {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.te__teaching {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.te__unsaved {
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  white-space: nowrap;
}
</style>
