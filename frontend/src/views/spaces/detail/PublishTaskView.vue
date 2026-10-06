<script setup lang="ts">
// 发题页的画面。取数、上传、发布都在容器（`PublishTask.vue`）里，这里只吃 props、只往上
// 发事件。
//
// 一页从上往下就是一道题：题目内容（右上角「从文件导入」「使用模板」）、参与、时间、
// 分类与话题、更多设置。从文件里读出好几道时，上面多一排题目列表：点一道就在下面改它的
// 名称和描述，其余设置这几道共用，发布按钮写明发几道。
import type { PickedAttachment } from '@/composables/useAttachmentUploads'
import type { NavTarget } from '@/lib/navTarget'
import type { DomainGroup, SpaceCategory, SpaceTeaching, TaskFormSubmitData, Topic } from '@/types'
import type { SpaceMaterial, SpaceMaterialsState } from '@/types/spaces'
import type { PublishDraft } from './publishDrafts'

import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { draftGaps } from './publishDrafts'

import BaseButton from '@/components/base/BaseButton.vue'
import NavLink from '@/components/common/NavLink.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import TeachingFields from '@/components/common/TeachingFields.vue'
import TaskFormContent from '@/components/tasks/form/TaskFormContent.vue'
import TaskAttachmentPicker from '@/components/tasks/TaskAttachmentPicker.vue'
import TaskForm from '@/components/tasks/TaskForm.vue'

defineProps<{
  /** 空间装好之前不挂表单（分类、域名组都挂在它上面）。 */
  ready: boolean
  listTo: NavTarget
  formKey: number
  initialData: Partial<TaskFormSubmitData>
  classificationTopics: Topic[]
  categories: SpaceCategory[]
  selectedCategoryId?: number
  domainGroups: DomainGroup[]
  templates: { index: number; name: string; description: string }[]
  importing: boolean
  /** 读出的那份文件名；没导入过就是 `null`。 */
  importedFile: string | null
  attachments: PickedAttachment[]
  uploading: boolean
  maxFileBytes: number | null
  materials: SpaceMaterial[]
  materialsState: SpaceMaterialsState
  libraryTo: string
  publishing: boolean
}>()

const drafts = defineModel<PublishDraft[]>('drafts', { required: true })
const teaching = defineModel<SpaceTeaching>('teaching', { required: true })
/** 这道题单独写了 AI 指导；没有就沿用空间的默认。 */
const teachingOwn = defineModel<boolean>('teachingOwn', { required: true })

const emit = defineEmits<{
  cancel: []
  'use-template': [index: number]
  import: [file: File]
  'discard-import': []
  'add-attachments': [files: File[]]
  'remove-attachment': [id: number]
  submit: [data: TaskFormSubmitData]
}>()

const { t } = useI18n()

const form = ref<{ submit: () => void } | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)

/** 读出了不止一道：逐道改内容，其余共用。 */
const batch = computed(() => drafts.value.length > 1)
const current = ref(0)
const currentDraft = computed(() => drafts.value[current.value])
const picked = computed(() => drafts.value.filter((draft) => draft.picked))

/** 点过发布之后才标出缺的，打开页面不该满屏红字。 */
const attempted = ref(false)
const formInvalid = ref(0)
const draftsInvalid = computed(() => picked.value.filter((draft) => draftGaps(draft).length > 0).length)
const blocking = computed(() => (attempted.value ? formInvalid.value + draftsInvalid.value : 0))

function updateDraft(index: number, patch: Partial<PublishDraft>) {
  drafts.value = drafts.value.map((draft, i) => (i === index ? { ...draft, ...patch } : draft))
}

const draftName = computed({
  get: () => currentDraft.value?.name ?? '',
  set: (name: string | undefined) => updateDraft(current.value, { name: name ?? '' }),
})
const draftDescription = computed({
  get: () => currentDraft.value?.description ?? { type: 'doc', content: [] },
  set: (description) =>
    updateDraft(current.value, {
      description: typeof description === 'string' ? JSON.parse(description) : description,
    }),
})
const draftNameControl = computed(() =>
  attempted.value && currentDraft.value && !currentDraft.value.name.trim()
    ? { 'error-messages': [t('tasks.form.validation.nameRequired')] }
    : {}
)

function gapLabel(draft: PublishDraft): string | null {
  const gaps = draftGaps(draft)
  if (gaps.includes('name')) return t('spaces.detail.publishTask.drafts.missingName')
  if (gaps.includes('description')) return t('spaces.detail.publishTask.drafts.missingDescription')
  return null
}

function publish() {
  attempted.value = true
  form.value?.submit()
}

function onSubmit(data: TaskFormSubmitData) {
  if (batch.value && (picked.value.length === 0 || draftsInvalid.value > 0)) return
  emit('submit', data)
}

function onFilePicked(event: Event) {
  const target = event.target as HTMLInputElement
  const file = target.files?.[0]
  target.value = ''
  if (file) emit('import', file)
}

const publishLabel = computed(() =>
  batch.value
    ? t('spaces.detail.publishTask.publishMany', { n: picked.value.length })
    : t('spaces.detail.publishTask.publish')
)

function discardImport() {
  current.value = 0
  emit('discard-import')
}
</script>

<template>
  <div class="pub">
    <PageHeader show-on-mobile>
      <nav class="pub__crumb">
        <NavLink :to="listTo" class="pub__crumb-parent">{{ t('spaces.detail.allContests') }}</NavLink>
        <v-icon size="16" class="pub__crumb-sep">mdi-chevron-right</v-icon>
        <span class="pub__crumb-here">{{ t('tasks.publish.title') }}</span>
      </nav>
      <template #actions>
        <span v-if="blocking" class="pub__blocking" data-testid="publish-blocking">{{
          t('spaces.detail.publishTask.blocking', { n: blocking })
        }}</span>
        <span v-else class="pub__note">{{ t('spaces.detail.publishTask.needsReview') }}</span>
        <BaseButton kind="ghost" :disabled="publishing" @click="emit('cancel')">{{ t('global.cancel') }}</BaseButton>
        <BaseButton
          kind="primary"
          :loading="publishing"
          :disabled="!ready || uploading || importing || (batch && picked.length === 0)"
          data-testid="publish-submit"
          @click="publish"
          >{{ publishLabel }}</BaseButton
        >
      </template>
    </PageHeader>

    <div class="pub__page">
      <!-- 从文件里读出了好几道：逐道勾、逐道改。 -->
      <section v-if="batch" class="pub__drafts" data-testid="publish-drafts">
        <div class="pub__drafts-head">
          <v-icon size="16" class="pub__drafts-icon">mdi-file-document-outline</v-icon>
          <span class="pub__drafts-source">{{
            t('spaces.detail.publishTask.drafts.source', {
              file: importedFile ?? '',
              total: drafts.length,
              picked: picked.length,
            })
          }}</span>
          <BaseButton kind="ghost" size="sm" :disabled="importing" @click="fileInput?.click()">{{
            t('spaces.detail.publishTask.drafts.replace')
          }}</BaseButton>
          <BaseButton kind="ghost" size="sm" @click="discardImport">{{
            t('spaces.detail.publishTask.drafts.discard')
          }}</BaseButton>
        </div>
        <ul class="pub__draft-list">
          <li
            v-for="(draft, index) in drafts"
            :key="draft.key"
            class="pub__draft"
            :class="{ 'pub__draft--current': index === current, 'pub__draft--off': !draft.picked }"
          >
            <v-checkbox-btn
              :model-value="draft.picked"
              density="compact"
              :aria-label="
                t('spaces.detail.publishTask.drafts.pick', {
                  name: draft.name || t('spaces.detail.publishTask.drafts.untitled'),
                })
              "
              @update:model-value="updateDraft(index, { picked: Boolean($event) })"
            />
            <button type="button" class="pub__draft-open" :aria-current="index === current" @click="current = index">
              <span class="pub__draft-n t-num">{{ index + 1 }}</span>
              <span class="pub__draft-name">{{ draft.name || t('spaces.detail.publishTask.drafts.untitled') }}</span>
              <span
                v-if="draft.picked && gapLabel(draft)"
                class="pub__draft-gap"
                :class="{ 'pub__draft-gap--error': attempted }"
                >{{ gapLabel(draft) }}</span
              >
              <span class="pub__draft-page t-meta-read">{{
                t('spaces.detail.publishTask.drafts.page', { n: draft.page })
              }}</span>
            </button>
          </li>
        </ul>
      </section>

      <TaskFormContent
        v-if="batch && currentDraft"
        :key="currentDraft.key"
        v-model:name="draftName"
        v-model:description="draftDescription"
        :name-control="draftNameControl"
        :title="t('spaces.detail.publishTask.drafts.content', { n: current + 1 })"
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
        :parameters-only="batch"
        :teaching-custom="teachingOwn"
        @invalid="formInvalid = $event"
        @submit="onSubmit"
      >
        <template #content-actions>
          <BaseButton
            kind="secondary"
            size="sm"
            prepend-icon="mdi-tray-arrow-up"
            :loading="importing"
            data-testid="publish-import"
            @click="fileInput?.click()"
            >{{ t('spaces.detail.publishTask.import') }}</BaseButton
          >
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
          <TaskAttachmentPicker
            :files="attachments"
            :uploading="uploading"
            :max-file-bytes="maxFileBytes"
            @add="emit('add-attachments', $event)"
            @remove="emit('remove-attachment', $event)"
          />
        </template>

        <template v-if="batch" #shared>
          <p class="pub__shared">{{ t('spaces.detail.publishTask.drafts.shared', { n: picked.length }) }}</p>
          <div class="pub__shared-attachments">
            <TaskAttachmentPicker
              :files="attachments"
              :uploading="uploading"
              :max-file-bytes="maxFileBytes"
              @add="emit('add-attachments', $event)"
              @remove="emit('remove-attachment', $event)"
            />
          </div>
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

      <input
        ref="fileInput"
        type="file"
        accept=".pdf,application/pdf"
        hidden
        data-testid="publish-import-input"
        @change="onFilePicked"
      />
    </div>
  </div>
</template>

<style scoped>
.pub {
  display: flex;
  flex-direction: column;
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

.pub__note {
  color: var(--faint);
  font-size: 12px;
  line-height: var(--lh-12);
}

.pub__blocking {
  color: var(--danger-ink);
  font-size: 12px;
  line-height: var(--lh-12);
}

.pub__page {
  width: min(var(--page-w), 100%);
  margin: 0 auto;
  padding: 8px 16px 64px;
}

.pub__drafts {
  margin-top: 16px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  overflow: hidden;
}

.pub__drafts-head {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  padding: 8px 12px 8px 16px;
  border-bottom: 1px solid var(--line);
  background: var(--fill);
}

.pub__drafts-icon {
  color: var(--muted);
}

.pub__drafts-source {
  flex: 1;
  min-width: 0;
  color: var(--text);
  font-size: 14px;
  line-height: var(--lh-14);
}

.pub__draft-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.pub__draft {
  display: flex;
  gap: 4px;
  align-items: center;
  padding-left: 8px;
}

.pub__draft :deep(.v-selection-control) {
  flex: none;
}

.pub__choices :deep(.v-label) {
  color: var(--text);
  font-size: 14px;
  line-height: var(--lh-14);
  opacity: 1;
}

.pub__draft + .pub__draft {
  border-top: 1px solid var(--line);
}

.pub__draft--current {
  background: var(--accent-wash);
}

.pub__draft-open {
  display: flex;
  flex: 1;
  gap: 12px;
  align-items: center;
  min-width: 0;
  height: 44px;
  padding: 0 16px 0 4px;
  border: 0;
  background: none;
  color: var(--text);
  font: inherit;
  font-size: 14px;
  text-align: left;
  cursor: pointer;
}

.pub__draft--current .pub__draft-name {
  color: var(--ink);
  font-weight: 600;
}

.pub__draft--off .pub__draft-name {
  color: var(--faint);
  text-decoration: line-through;
}

.pub__draft-n {
  width: 20px;
  color: var(--faint);
  font-size: 12px;
}

.pub__draft-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pub__draft-gap {
  flex: none;
  padding: 0 6px;
  border-radius: var(--radius-sm);
  background: var(--warn-wash);
  color: var(--warn-ink);
  font-size: 12px;
  line-height: var(--lh-12);
}

.pub__draft-gap--error {
  background: var(--danger-wash);
  color: var(--danger-ink);
}

.pub__draft-page {
  flex: none;
  margin-left: auto;
}

.pub__shared {
  display: flex;
  gap: 8px;
  align-items: center;
  margin: 24px 0 0;
  color: var(--faint);
  font-size: 12px;
  font-weight: 600;
  line-height: var(--lh-12);
}

.pub__shared::after {
  flex: 1;
  height: 1px;
  background: var(--line);
  content: '';
}

.pub__shared-attachments {
  padding: 24px 0;
  border-bottom: 1px solid var(--line);
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
