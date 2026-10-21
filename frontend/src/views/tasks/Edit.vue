<script setup lang="ts">
// 改题页：和发题页同一张表（`TaskForm`），参与方式改不了，附件直接传到这道题上、从这道
// 题上摘，AI 指导和发题时一样单独写或沿用空间的默认。没有导入和模板。
//
// 改动先存成这道题的修改草稿（`useTaskDraft`）：边改边存，离开再回来还在，点「保存更改」
// 才落到题目上，学生在那之前看到的是原来的题目。芝士停在右边，接着的是题目页「问芝士」
// 的那段对话；它改的也是这份草稿，表单跟着动（`useDraftAgent`）。
import type { AgentBox, AgentMark, AgentPointer } from '@/components/tasks/AgentLayer.vue'
import type { SpaceTeaching, TaskDraftFields, TaskFormSubmitData } from '@/types'

import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'
import { toast } from 'vuetify-sonner'
import { storeToRefs } from 'pinia'

import { useAgentPointer } from '@/composables/useAgentPointer'
import { useAttachmentUploads } from '@/composables/useAttachmentUploads'
import { useDraftAgent } from '@/composables/useDraftAgent'
import { useSpaceData } from '@/composables/useSpaceData'
import { useSpaceMaterials } from '@/composables/useSpaceMaterials'
import { useTaskDraft } from '@/composables/useTaskDraft'

import { useTaskData } from './composables'

import { TaskBoardApi } from '@/api/taskBoard'
import { TaskDraftsApi } from '@/api/taskDrafts'
import AgentEntryButton from '@/components/assistant/AgentEntryButton.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import TeachingFields from '@/components/common/TeachingFields.vue'
import AgentLayer from '@/components/tasks/AgentLayer.vue'
import TaskAttachmentPicker from '@/components/tasks/TaskAttachmentPicker.vue'
import TaskForm from '@/components/tasks/TaskForm.vue'
import { closeOverlay } from '@/lib/backOut'
import { spaceLibraryPath } from '@/lib/spaceRouteNames'
import { mentorOptions } from '@/lib/taskMentors'
import { isTeachingBlank } from '@/lib/teaching'
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

/** 附件直接传到这道题上、从这道题上摘：改题页上的增删当场生效，不等保存。 */
const uploads = useAttachmentUploads({
  upload: async (file) => (await TaskBoardApi.uploadAttachment(taskId, file)).attachment.id,
  remove: async (id) => {
    await TaskBoardApi.removeAttachment(taskId, id)
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
  draft.baseline({ teaching: teaching.value, teachingOwn: teachingOwn.value })
  formKey.value++
  draftReady.value = true
}

/** 有没有还没保存到题目上的修改。 */
const unsaved = computed(() => (draft.draft.value?.version ?? 0) > 0)

watch([teaching, teachingOwn], () => draft.changed({ teaching: teaching.value, teachingOwn: teachingOwn.value }), {
  deep: true,
})

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
            return f.apply(fields)
          },
        }
      : null
  )
)

// --- 芝士 -----------------------------------------------------------------------------

const terms = () => ({
  categories: editCategories.value.map((c) => ({ id: c.id, name: c.name })),
  topics: (taskData.value?.space?.classificationTopics ?? []).map((topic) => ({ id: topic.id, name: topic.name })),
  domainGroups: domainGroups.value.map((g) => ({ id: g.id, name: g.name })),
  mentors: mentorChoices.value.map((m) => ({ handle: m.value, name: m.title })),
})

const agent = useDraftAgent({
  draft,
  terms,
  publishedHere: () => navigateToDetail(),
  jump: (key) => layer.jump(key),
  openTask: (id) => openTask(id),
})
const { assistant } = agent
// 芝士读到的是这份修改草稿；面板在这一页给改题的开场问题，能交文件。
useAgentContext(() => ({
  page: draft.id.value ? { kind: 'draft', id: draft.id.value } : null,
  starters: [
    t('tasks.assistant.draft.editStarters.later'),
    t('tasks.assistant.draft.editStarters.rubric'),
    t('tasks.assistant.draft.editStarters.check'),
  ],
  placeholder: t('tasks.assistant.draft.placeholder'),
  hint: t('tasks.assistant.draft.hint'),
  canAttach: true,
}))

const layer = useAgentPointer({
  host,
  writing: draft.writing,
  marks: draft.marks,
  active: computed(() => assistant.busy.value),
  descriptionEnd: () => form.value?.descriptionEnd() ?? null,
  passage: draft.passage,
  holding: draft.holding,
})
const pointer = computed<AgentPointer | null>(() => layer.pointer.value)
const outline = computed<AgentBox | null>(() => layer.outline.value)
const marks = computed<AgentMark[]>(() => layer.marks.value)
// 桌面上进这一页时，芝士停在右边就把它打开：表单和面板并排，芝士写进来看得见。
onMounted(() => {
  if (mdAndUp.value && agentDocked.value) openAgent()
})

// --- 保存 ---------------------------------------------------------------------------

const formInvalid = ref(0)
const attempted = ref(false)
const saving = ref(false)
const resubmit = ref(false)

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
  try {
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
    void errorHandler.handle(err as Error, { defaultMessage: t('global.updateFailed') })
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
    const data = await TaskBoardApi.listAttachments(taskId)
    uploads.reset(data.attachments.map(({ id, name, size }) => ({ id, name, size })))
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
      :classification-topics="taskData.space?.classificationTopics || []"
      :categories="editCategories"
      :domain-groups="domainGroups"
      :teaching-custom="teachingOwn"
      :mentor-options="mentorChoices"
      :agent-writing="draft.writing.value"
      @invalid="formInvalid = $event"
      @submit="onSubmit"
      @change="draft.changed"
      @ready="draft.baseline"
    >
      <template #attachments>
        <TaskAttachmentPicker
          :files="uploads.files.value"
          :uploading="uploads.uploading.value"
          :max-file-bytes="uploads.maxFileBytes.value"
          @add="uploads.add"
          @remove="uploads.remove"
        />
      </template>
      <template #teaching>
        <div data-agent-field="ai_guidance">
          <p class="te__label">{{ t('spaces.detail.publishTask.teaching.title') }}</p>
          <p class="te__hint t-meta-read">{{ t('spaces.detail.publishTask.teaching.subtitle') }}</p>
          <TeachingFields
            :model-value="teaching"
            class="te__teaching"
            inherit
            :space-teaching="spaceStore.currentSpace?.teaching ?? null"
            :materials="materials"
            :materials-state="materialsState"
            :library-to="spaceLibraryPath(spaceId)"
            @update:model-value="setTeaching"
          />
        </div>
      </template>
    </TaskForm>
    <AgentLayer
      :pointer="pointer"
      :outline="outline"
      :marks="marks"
      :selection="layer.selection.value"
      :name="t('work.agentAvatar.defaultName')"
      @undo="agent.undoMark"
    />
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

.te__label {
  margin: 0;
  color: var(--text);
  font-size: 13px;
  font-weight: 500;
  line-height: var(--lh-13);
}

.te__hint {
  margin: 4px 0 8px;
}

.te__teaching {
  margin-top: 12px;
}

.te__unsaved {
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  white-space: nowrap;
}
</style>
