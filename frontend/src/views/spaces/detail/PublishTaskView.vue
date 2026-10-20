<script setup lang="ts">
// 发题页的画面：左边是那张表单（上面一层是芝士的指针和它填过的标记），右边停靠着芝士。
// 取数、存草稿、发布、芝士的对话都在容器（`PublishTask.vue`）里，这里只吃 props、只往上
// 发事件；芝士的面板由容器塞进 `agent` 插槽。
//
// 题目边写边存成草稿（`@/types/taskDrafts`）。顶栏的「草稿」列出我在这个空间的草稿，
// 点哪份打开哪份；旁边写着存到哪了。
import type { AgentBox, AgentMark, AgentPointer } from '@/components/tasks/AgentLayer.vue'
import type { PickedAttachment } from '@/composables/useAttachmentUploads'
import type { SaveState } from '@/composables/useTaskDraft'
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

import BaseButton from '@/components/base/BaseButton.vue'
import NavLink from '@/components/common/NavLink.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import TeachingFields from '@/components/common/TeachingFields.vue'
import AgentLayer from '@/components/tasks/AgentLayer.vue'
import TaskAttachmentPicker from '@/components/tasks/TaskAttachmentPicker.vue'
import TaskForm from '@/components/tasks/TaskForm.vue'

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
    attachments: PickedAttachment[]
    uploading: boolean
    maxFileBytes: number | null
    materials: SpaceMaterial[]
    materialsState: SpaceMaterialsState
    libraryTo: string
    publishing: boolean
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
    /** 芝士那一层。 */
    pointer: AgentPointer | null
    outline: AgentBox | null
    marks: AgentMark[]
    /** 芝士选中、要换掉的那段字。 */
    selection?: AgentBox[]
    agentName: string
  }>(),
  {
    selectedCategoryId: undefined,
    mentorOptions: () => [],
    defaultMentor: undefined,
    publishedHere: false,
    strip: null,
    selection: () => [],
  }
)

const teaching = defineModel<SpaceTeaching>('teaching', { required: true })
/** 这道题单独写了 AI 指导；没有就沿用空间的默认。 */
const teachingOwn = defineModel<boolean>('teachingOwn', { required: true })
/** 窄屏上芝士是一张从下面拉起来的面板。 */
const agentOpen = defineModel<boolean>('agentOpen', { default: false })

const emit = defineEmits<{
  cancel: []
  'use-template': [index: number]
  'add-attachments': [files: File[]]
  'remove-attachment': [id: number]
  submit: [data: TaskFormSubmitData]
  change: [fields: TaskDraftFields]
  'form-ready': [fields: TaskDraftFields]
  'open-draft': [id: number]
  'new-draft': []
  'remove-draft': [id: number]
  'publish-all': []
  undo: [key: string]
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
  /** 表单：读现在的样子、填进几格、详情末尾在哪。 */
  form: {
    values: () => form.value?.values() ?? {},
    apply: async (fields: TaskDraftFields) => (await form.value?.apply(fields)) ?? {},
  },
  descriptionEnd: () => form.value?.descriptionEnd() ?? null,
  /** 标出这一道缺的格子（一次发几道、这道没发出去时）。 */
  check: () => {
    attempted.value = true
    void form.value?.check()
  },
  /** 芝士那一层量位置用的外框。 */
  host,
})
</script>

<template>
  <div class="pub">
    <div>
      <PageHeader show-on-mobile>
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
          <span v-else class="pub__note">{{ t('spaces.detail.publishTask.needsReview') }}</span>
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
            :teaching-custom="teachingOwn"
            :mentor-options="mentorOptions"
            :default-mentor="defaultMentor"
            @invalid="formInvalid = $event"
            @submit="emit('submit', $event)"
            @change="emit('change', $event)"
            @ready="emit('form-ready', $event)"
          >
            <template v-if="templates.length" #content-actions>
              <v-menu location="bottom end">
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
              <TaskAttachmentPicker
                :files="attachments"
                :uploading="uploading"
                :max-file-bytes="maxFileBytes"
                @add="emit('add-attachments', $event)"
                @remove="emit('remove-attachment', $event)"
              />
            </template>

            <template #teaching>
              <div class="pub__teaching" data-testid="publish-teaching">
                <p class="pub__teaching-label">{{ t('spaces.detail.publishTask.teaching.title') }}</p>
                <p class="pub__teaching-hint t-meta-read">{{ t('spaces.detail.publishTask.teaching.subtitle') }}</p>
                <v-radio-group v-model="teachingOwn" hide-details density="compact" class="pub__choices">
                  <v-radio :value="false" :label="t('spaces.detail.publishTask.teaching.inherit')" />
                  <v-radio :value="true" :label="t('spaces.detail.publishTask.teaching.own')" />
                </v-radio-group>
                <TeachingFields
                  v-if="teachingOwn"
                  v-model="teaching"
                  class="pub__teaching-fields"
                  :materials="materials"
                  :materials-state="materialsState"
                  :library-to="libraryTo"
                />
              </div>
            </template>
          </TaskForm>

          <AgentLayer
            :pointer="pointer"
            :outline="outline"
            :marks="marks"
            :selection="selection"
            :name="agentName"
            @undo="emit('undo', $event)"
          />
        </div>
      </div>
    </div>

    <!-- Docked like the task page's 问芝士: the drawer registers with the page frame. -->
    <v-navigation-drawer v-if="mdAndUp" permanent location="right" width="380" class="pub__agent">
      <slot name="agent" />
    </v-navigation-drawer>
    <template v-else>
      <BaseButton
        class="pub__agent-open"
        kind="secondary"
        prepend-icon="mdi-creation-outline"
        data-testid="publish-agent-open"
        @click="agentOpen = true"
        >{{ agentName }}</BaseButton
      >
      <v-bottom-sheet v-model="agentOpen">
        <div class="pub__sheet"><slot name="agent" /></div>
      </v-bottom-sheet>
    </template>
  </div>
</template>

<style scoped>
.pub {
  min-height: 100%;
  background: var(--surface);
}

.pub__crumb {
  display: flex;
  gap: 4px;
  align-items: center;
  min-width: 0;
  font-size: 14px;
  line-height: var(--lh-14);
}

.pub__crumb-parent {
  color: var(--muted);
  text-decoration: none;
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

.pub__agent :deep(.v-navigation-drawer__content) {
  display: flex;
  flex-direction: column;
}

.pub__agent-open {
  position: fixed;
  right: 16px;
  bottom: calc(16px + env(safe-area-inset-bottom));
  z-index: 5;
}

.pub__sheet {
  height: 78vh;
  background: var(--surface);
}

.pub__choices :deep(.v-label) {
  color: var(--text);
  font-size: 14px;
  line-height: var(--lh-14);
  opacity: 1;
}

.pub__teaching-label {
  margin: 0;
  color: var(--text);
  font-size: 13px;
  font-weight: 500;
  line-height: var(--lh-13);
}

.pub__teaching-hint {
  margin: 4px 0 8px;
}

.pub__teaching-fields {
  margin-top: 12px;
}
</style>
