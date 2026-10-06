<script setup lang="ts">
// 改题页的画面。读地址、取题、装空间、传附件、保存、跳转都在容器（`Edit.vue`）里 ——
// 这里只吃 props、只往上发事件。
//
// 一页从上往下：面包屑和页头那三颗按钮（取消 / 保存并重新提交 / 保存），下面是那张和
// 发题页共用的表（`TaskForm`）。附件直接传到这道题上、从这道题上摘；这道题可以单独写
// 一份 AI 指导，不写就沿用空间的默认。
import type { PickedAttachment } from '@/composables/useAttachmentUploads'
import type { NavTarget } from '@/lib/navTarget'
import type { DomainGroup, SpaceCategory, SpaceTeaching, TaskFormSubmitData, Topic } from '@/types'
import type { SpaceMaterial, SpaceMaterialsState } from '@/types/spaces'

import { ref } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import TeachingFields from '@/components/common/TeachingFields.vue'
import TaskAttachmentPicker from '@/components/tasks/TaskAttachmentPicker.vue'
import TaskForm from '@/components/tasks/TaskForm.vue'

defineProps<{
  loading: boolean
  error: string | null
  /** 题目数据到位了才画表单；没有就什么都不画（加载中和读失败已经各自占了一格）。 */
  hasTaskData: boolean
  /** 面包屑中间那一节写的题名。 */
  taskName: string
  /** 审核没通过的题：页头上多一颗「保存并重新提交」。 */
  rejected: boolean
  saving: boolean
  listTo: NavTarget
  detailTo: NavTarget
  /** 表单的初值，和容器里那一份 `editTaskData` 是同一份。 */
  initialData: Partial<TaskFormSubmitData> | null
  classificationTopics: Topic[]
  /** 活跃的分类，加上这道题现在所在的那一个（已归档也得显示得出来）。 */
  categories: SpaceCategory[]
  domainGroups: DomainGroup[]
  materials: SpaceMaterial[]
  materialsState: SpaceMaterialsState
  /** 空间资料库的地址，「从资料库选」用。 */
  libraryTo: string
  attachments: PickedAttachment[]
  uploading: boolean
  maxFileBytes: number | null
}>()

const teaching = defineModel<SpaceTeaching>('teaching', { required: true })
/** 这道题单独写了 AI 指导；没有就沿用空间（或项目集）的默认。 */
const teachingOwn = defineModel<boolean>('teachingOwn', { required: true })

const emit = defineEmits<{
  retry: []
  cancel: []
  /** 表单过了校验。`resubmit` 说的是按的哪一颗按钮，容器据此决定要不要一并重新送审。 */
  submit: [data: TaskFormSubmitData, resubmit: boolean]
  'add-attachments': [files: File[]]
  'remove-attachment': [id: number]
}>()

const { t } = useI18n()

const form = ref<{ submit: () => void } | null>(null)
/** 点过保存之后才标出拦着的项数：打开页面不该满屏红字。 */
const attempted = ref(false)
const formInvalid = ref(0)
/** 这一趟提交按的是「保存并重新提交」那一颗。 */
const resubmit = ref(false)

// 按钮不在 `<form>` 里（它们挂在页头的操作区），所以由这里催表单自己走一遍原生提交：
// `TaskForm` 校验、标红，过了才把数据发上来。
function save(again: boolean) {
  attempted.value = true
  resubmit.value = again
  form.value?.submit()
}
</script>

<template>
  <PageHeader show-on-mobile>
    <nav class="te__crumb">
      <router-link :to="listTo" class="te__crumb-link">{{ t('spaces.detail.allContests') }}</router-link>
      <v-icon size="16" class="te__crumb-sep">mdi-chevron-right</v-icon>
      <router-link :to="detailTo" class="te__crumb-link" data-user-content>{{ taskName }}</router-link>
      <v-icon size="16" class="te__crumb-sep">mdi-chevron-right</v-icon>
      <span class="te__crumb-here">{{ t('tasks.edit.crumb') }}</span>
    </nav>
    <template #actions>
      <span v-if="attempted && formInvalid" class="te__blocking">{{
        t('spaces.detail.publishTask.blocking', { n: formInvalid })
      }}</span>
      <BaseButton kind="ghost" :disabled="saving" @click="emit('cancel')">{{ t('global.cancel') }}</BaseButton>
      <BaseButton
        v-if="rejected"
        kind="secondary"
        :loading="saving && resubmit"
        :disabled="saving || uploading"
        @click="save(true)"
        >{{ t('tasks.edit.saveAndResubmit') }}</BaseButton
      >
      <BaseButton
        kind="primary"
        :loading="saving && !resubmit"
        :disabled="saving || uploading || !hasTaskData"
        @click="save(false)"
        >{{ t('tasks.edit.saveChanges') }}</BaseButton
      >
    </template>
  </PageHeader>

  <div class="te">
    <div v-if="loading" class="py-12 text-center">
      <v-progress-circular indeterminate color="primary" />
    </div>
    <!-- A failed read trades the form for the error (docs/design-system.md §3.10). -->
    <BaseLoadError v-else-if="error" :title="t('tasks.loadError.title')" :error="error" @retry="emit('retry')" />
    <TaskForm
      v-else-if="hasTaskData"
      ref="form"
      :initial-data="initialData"
      is-editing
      :classification-topics="classificationTopics"
      :categories="categories"
      :domain-groups="domainGroups"
      :teaching-custom="teachingOwn"
      @invalid="formInvalid = $event"
      @submit="emit('submit', $event, resubmit)"
    >
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
        <div>
          <p class="te__label">{{ t('spaces.detail.publishTask.teaching.title') }}</p>
          <p class="te__hint t-meta-read">{{ t('spaces.detail.publishTask.teaching.subtitle') }}</p>
          <v-radio-group v-model="teachingOwn" hide-details density="compact" class="te__choices">
            <v-radio :value="false" :label="t('spaces.detail.publishTask.teaching.inherit')" />
            <v-radio :value="true" :label="t('spaces.detail.publishTask.teaching.own')" />
          </v-radio-group>
          <TeachingFields
            v-if="teachingOwn"
            v-model="teaching"
            class="te__teaching"
            :materials="materials"
            :materials-state="materialsState"
            :library-to="libraryTo"
          />
        </div>
      </template>
    </TaskForm>
  </div>
</template>

<style scoped>
.te {
  width: min(var(--page-w), 100%);
  margin: 0 auto;
  padding: 8px 16px 64px;
}

.te__crumb {
  display: flex;
  gap: 4px;
  align-items: center;
  min-width: 0;
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

.te__choices :deep(.v-label) {
  color: var(--text);
  font-size: 14px;
  line-height: var(--lh-14);
  opacity: 1;
}

.te__teaching {
  margin-top: 12px;
}
</style>
