<script setup lang="ts">
// 发题页的画面：左边是那张表单（上面一层是芝士的指针和它填过的标记），右边停靠着芝士。
// 取数、存草稿、发布、芝士的对话都在容器（`PublishTask.vue`）里，这里只吃 props、只往上
// 发事件；芝士的面板由容器塞进 `agent` 插槽。
//
// 题目边写边存成草稿（`@/types/taskDrafts`）。顶栏的「草稿」列出我在这个空间的草稿，
// 点哪份打开哪份；旁边写着存到哪了。
import type { PickedAttachment } from '@/composables/useAttachmentUploads'
import type { SkillChoice } from '@/composables/useSkillCatalog'
import type { SaveState } from '@/composables/useTaskDraft'
import type { AgentType } from '@/cx_types'
import type { NavTarget } from '@/lib/navTarget'
import type {
  DomainGroup,
  SpaceCategory,
  SpaceTeaching,
  TaskDraftFields,
  TaskDraftSummary,
  TaskFormSubmitData,
  Topic,
} from '@/types'
import type { SpaceMaterial, SpaceMaterialsState } from '@/types/spaces'
import type { StripItem } from './TaskDraftStrip.vue'

import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useDisplay } from 'vuetify'

import TaskDraftMenu from './TaskDraftMenu.vue'
import TaskDraftStrip from './TaskDraftStrip.vue'

import AgentEntryButton from '@/components/assistant/AgentEntryButton.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import DefaultTeammatePicker from '@/components/common/DefaultTeammatePicker.vue'
import NavLink from '@/components/common/NavLink.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import TeachingFields from '@/components/common/TeachingFields.vue'
import TaskAttachmentPicker from '@/components/tasks/TaskAttachmentPicker.vue'
import TaskForm from '@/components/tasks/TaskForm.vue'
import TaskStarterPicker from '@/components/tasks/TaskStarterPicker.vue'
import { isTeachingBlank } from '@/lib/teaching'

const props = withDefaults(
  defineProps<{
    /** 空间和草稿装好之前不挂表单（分类、域名组都挂在空间上）。 */
    ready: boolean
    listTo: NavTarget
    formKey: number
    initialData: Partial<TaskFormSubmitData>
    classificationTopics: Topic[]
    categories: SpaceCategory[]
    selectedCategoryId?: number
    domainGroups: DomainGroup[]
    templates: { index: number; name: string; description: string }[]
    attachments: PickedAttachment<number>[]
    uploading: boolean
    /** 正在传的那一份材料传到了百分之几；`null` = 没在传。 */
    uploadPercent?: number | null
    maxFileBytes: number | null
    /** 起始代码：选好的那一份 .zip，它是否正在上传、传到了百分之几。 */
    starter?: { name: string; size: number } | null
    starterUploading?: boolean
    starterPercent?: number | null
    materials: SpaceMaterial[]
    materialsState: SpaceMaterialsState
    libraryTo: string
    publishing: boolean
    /** 上一次发布没成的原因（服务端的原话）；没有就是空串。 */
    publishError?: string
    /** 能当导师的人和默认的那一位（出题人自己，#3830）。 */
    mentorOptions?: { title: string; value: string }[]
    defaultMentor?: string
    /** 我在这个空间的草稿，和眼前这份。 */
    drafts: TaskDraftSummary[]
    currentDraftId: number | null
    saveState: SaveState
    /** 芝士已经把眼前这份草稿发布了。 */
    publishedHere?: boolean
    /** 一份文件拆出的几道题：表单上方那一排；不是这种情况时是 null。 */
    strip?: {
      items: StripItem[]
      source: string
      flagged: boolean
      publishing: boolean
      note: { text: string; ok: boolean } | null
    } | null
    /** 芝士正在写的那一格（它的字段名）。 */
    agentWriting?: string | null
    /** 空间的 AI 队友辅导：和本题的同时生效，摆在本题设置底下。 */
    spaceTeaching?: SpaceTeaching | null
    agentName: string
    /** 「默认队友」那一格的候选预设：容器取好传进来（这一屏不碰 API 层）。 */
    agentTypes?: AgentType[]
    agentTypesFailed?: boolean
    /** 「技能」那一格的候选，同样由容器取好传进来。 */
    skillCatalog?: SkillChoice[]
    skillCatalogFailed?: boolean
    /** 选中的项目集设的默认队友（`null` = 没设；不给 = 还没选项目集）。 */
    inheritedRole?: string | null
  }>(),
  {
    selectedCategoryId: undefined,
    uploadPercent: null,
    starter: null,
    starterUploading: false,
    starterPercent: null,
    inheritedRole: undefined,
    mentorOptions: () => [],
    defaultMentor: undefined,
    publishedHere: false,
    publishError: '',
    strip: null,
    agentWriting: null,
    spaceTeaching: null,
    agentTypes: () => [],
    agentTypesFailed: false,
    skillCatalog: () => [],
    skillCatalogFailed: false,
  }
)

const teaching = defineModel<SpaceTeaching>('teaching', { required: true })
/** 这道题单独写了 AI 指导；没有就沿用空间的默认。 */
const teachingOwn = defineModel<boolean>('teachingOwn', { required: true })
/** 这道题指定的默认队友（#944）：`''` = 这一层不单独指定。发题页没有原始值。 */
const defaultRole = defineModel<string>('defaultRole', { required: true })

/** 题目和空间的辅导设置同时生效：这道题写了什么，就算「本题有自己的」。 */
function setTeaching(value: SpaceTeaching) {
  teaching.value = value
  teachingOwn.value = !isTeachingBlank(value)
}
/** 芝士的面板开着（面板是全站那一个，不在这一页里）：开着时页头不再给打开它的按钮。 */
const agentOpen = defineModel<boolean>('agentOpen', { default: false })

const emit = defineEmits<{
  cancel: []
  'use-template': [index: number]
  /** 「从文件导入」选好了文件：交给芝士去读、去填。 */
  'import-files': [files: File[]]
  'add-attachments': [files: File[]]
  'remove-attachment': [id: number]
  'pick-starter': [file: File]
  'remove-starter': []
  submit: [data: TaskFormSubmitData]
  change: [fields: TaskDraftFields]
  'form-ready': [fields: TaskDraftFields]
  'open-draft': [id: number]
  'new-draft': []
  'remove-draft': [id: number]
  'publish-all': []
}>()

defineSlots<{
  /** 芝士的面板。 */
  agent: () => unknown
}>()

const { t } = useI18n()
const { mdAndUp } = useDisplay()

type FormHandle = InstanceType<typeof TaskForm>
const form = ref<FormHandle | null>(null)
const host = ref<HTMLElement | null>(null)
const importInput = ref<HTMLInputElement | null>(null)

function onImport(e: Event) {
  const input = e.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  input.value = ''
  if (files.length) emit('import-files', files)
}

/** 点过发布之后才标出缺的，打开页面不该满屏红字。 */
const attempted = ref(false)
const formInvalid = ref(0)
const blocking = computed(() => (attempted.value ? formInvalid.value : 0))

function publish() {
  attempted.value = true
  form.value?.submit()
}

const saveText = computed(() => {
  if (props.publishedHere) return t('spaces.detail.publishTask.save.published')
  switch (props.saveState) {
    case 'saving':
      return t('spaces.detail.publishTask.save.saving')
    case 'saved':
      return t('spaces.detail.publishTask.save.saved')
    case 'failed':
      return t('spaces.detail.publishTask.save.failed')
    default:
      return ''
  }
})

defineExpose({
  /** 表单：读现在的样子、填进几格。 */
  form: {
    values: () => form.value?.values() ?? {},
    apply: async (fields: TaskDraftFields) => (await form.value?.apply(fields)) ?? {},
  },
  /** 标出这一道缺的格子（一次发几道、这道没发出去时）。 */
  check: () => {
    attempted.value = true
    void form.value?.check()
  },
  /** 表单的外框：芝士那一层（全站的芝士宿主画）放在这里。 */
  host,
})
</script>

<template>
  <div class="pub">
    <div>
      <PageHeader show-on-mobile class="pub__head">
        <nav class="pub__crumb">
          <NavLink :to="listTo" class="pub__crumb-parent">{{ t('spaces.detail.allContests') }}</NavLink>
          <v-icon size="16" class="pub__crumb-sep">mdi-chevron-right</v-icon>
          <span class="pub__crumb-here">{{ t('tasks.publish.title') }}</span>
        </nav>
        <TaskDraftMenu
          :drafts="drafts"
          :current-id="currentDraftId"
          @open="emit('open-draft', $event)"
          @new="emit('new-draft')"
        />
        <template #actions>
          <span
            v-if="saveText"
            class="pub__saved"
            :class="{ 'pub__saved--bad': saveState === 'failed' }"
            data-testid="publish-save-state"
            >{{ saveText }}</span
          >
          <span v-if="blocking" class="pub__blocking" data-testid="publish-blocking">{{
            t('spaces.detail.publishTask.blocking', { n: blocking })
          }}</span>
          <span v-else-if="publishError" class="pub__blocking" role="alert" data-testid="publish-failed">{{
            publishError
          }}</span>
          <span v-else class="pub__note">{{ t('spaces.detail.publishTask.needsReview') }}</span>
          <AgentEntryButton
            v-if="mdAndUp && !agentOpen"
            :name="agentName"
            data-testid="publish-agent-open"
            @click="agentOpen = true"
          />
          <BaseButton kind="ghost" :disabled="publishing" @click="emit('cancel')">{{ t('global.cancel') }}</BaseButton>
          <BaseButton
            kind="primary"
            :loading="publishing"
            :disabled="!ready || uploading || publishedHere"
            data-testid="publish-submit"
            @click="publish"
            >{{ t('spaces.detail.publishTask.publish') }}</BaseButton
          >
        </template>
      </PageHeader>

      <div>
        <div ref="host" class="pub__page">
          <TaskDraftStrip
            v-if="strip"
            :items="strip.items"
            :current-id="currentDraftId"
            :source="strip.source"
            :flagged="strip.flagged"
            :publishing="strip.publishing"
            :note="strip.note"
            @open="emit('open-draft', $event)"
            @remove="emit('remove-draft', $event)"
            @publish-all="emit('publish-all')"
          />
          <TaskForm
            v-if="ready"
            ref="form"
            :key="formKey"
            :initial-data="initialData"
            :classification-topics="classificationTopics"
            :categories="categories"
            :selected-category-id="selectedCategoryId"
            :domain-groups="domainGroups"
            :mentor-options="mentorOptions"
            :default-mentor="defaultMentor"
            :agent-writing="agentWriting"
            @invalid="formInvalid = $event"
            @submit="emit('submit', $event)"
            @change="emit('change', $event)"
            @ready="emit('form-ready', $event)"
          >
            <template #content-actions>
              <BaseButton
                kind="secondary"
                size="sm"
                prepend-icon="mdi-file-import-outline"
                data-testid="publish-import"
                @click="importInput?.click()"
                >{{ t('spaces.detail.publishTask.importFile') }}</BaseButton
              >
              <input
                ref="importInput"
                type="file"
                multiple
                hidden
                :aria-label="t('spaces.detail.publishTask.importFile')"
                @change="onImport"
              />
              <v-menu v-if="templates.length" location="bottom end">
                <template #activator="{ props: menu }">
                  <BaseButton v-bind="menu" kind="secondary" size="sm" append-icon="mdi-chevron-down">{{
                    t('spaces.detail.publishTask.useTemplate')
                  }}</BaseButton>
                </template>
                <v-list density="compact" min-width="240">
                  <v-list-item
                    v-for="template in templates"
                    :key="template.index"
                    :title="template.name"
                    :subtitle="template.description || undefined"
                    @click="emit('use-template', template.index)"
                  />
                </v-list>
              </v-menu>
            </template>

            <template #attachments>
              <div class="pub__files">
                <TaskAttachmentPicker
                  :files="attachments"
                  :uploading="uploading"
                  :percent="uploadPercent"
                  :max-file-bytes="maxFileBytes"
                  @add="emit('add-attachments', $event)"
                  @remove="emit('remove-attachment', Number($event))"
                />
                <TaskStarterPicker
                  :file="starter"
                  :uploading="starterUploading"
                  :percent="starterPercent"
                  :max-bytes="maxFileBytes"
                  @pick="emit('pick-starter', $event)"
                  @remove="emit('remove-starter')"
                />
              </div>
            </template>

            <template #teaching>
              <div class="pub__teaching" data-testid="publish-teaching" data-agent-field="ai_guidance">
                <DefaultTeammatePicker
                  v-model="defaultRole"
                  :types="agentTypes"
                  :types-failed="agentTypesFailed"
                  :inherited="inheritedRole"
                />
                <TeachingFields
                  :model-value="teaching"
                  inherit
                  :space-teaching="spaceTeaching"
                  :materials="materials"
                  :materials-state="materialsState"
                  :library-to="libraryTo"
                  :skill-catalog="skillCatalog"
                  :skill-catalog-failed="skillCatalogFailed"
                  @update:model-value="setTeaching"
                />
              </div>
            </template>
          </TaskForm>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.pub {
  min-height: 100%;
  background: var(--surface);
}

/* 左边这一栏多宽，取决于两侧的侧栏和芝士：顶栏按自己的宽窄收，不按窗口。 */
.pub__files {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.pub__head {
  container-type: inline-size;
}

.pub__crumb {
  display: flex;
  flex: 0 1 auto;
  gap: 4px;
  align-items: center;
  min-width: 0;
  overflow: hidden;
  font-size: 14px;
  line-height: var(--lh-14);
  white-space: nowrap;
}

.pub__crumb-parent {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--muted);
  text-decoration: none;
}

.pub__crumb-sep,
.pub__crumb-here {
  flex: none;
}

/* 窄的时候先让出说明，再让出上一级。 */
@container (max-width: 760px) {
  .pub__note {
    display: none;
  }
}

@container (max-width: 600px) {
  .pub__crumb-parent,
  .pub__crumb-sep {
    display: none;
  }
}

/* 手机上顶栏已经写着「发布题目」，这一行只留草稿和操作。 */
@container (max-width: 480px) {
  .pub__crumb {
    display: none;
  }
}

.pub__crumb-parent:hover {
  color: var(--ink);
}

.pub__crumb-sep {
  color: var(--faint);
}

.pub__crumb-here {
  color: var(--ink);
  font-weight: 600;
}

.pub__saved,
.pub__note {
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  white-space: nowrap;
}

.pub__saved--bad,
.pub__blocking {
  color: var(--danger-ink);
  font-size: 13px;
  line-height: var(--lh-13);
}

.pub__page {
  position: relative;
  width: min(var(--page-w), 100%);
  margin: 0 auto;
  padding: 8px 16px 96px;
}

.pub__teaching {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
</style>
