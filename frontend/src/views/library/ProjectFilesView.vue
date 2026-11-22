<script setup lang="ts">
// 资料库 › 项目文件的画面：页头写「资料库 / 项目文件」，旁边一行是最近一次改动，「改动
// 记录」看整个项目的；下面是目录树和开着的那一份（`ProjectFilesBrowser`）。
//
// **只认 props**：取数、地址、跳转都在同目录的 `ProjectFiles.vue`。
import type { ProjectChange, WorkspaceFile } from '@/cx_types'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import AppPage from '@/components/common/AppPage.vue'
import ProjectChangesDialog from '@/components/library/ProjectChangesDialog.vue'
import ProjectFilesBrowser from '@/components/library/ProjectFilesBrowser.vue'
import { t } from '@/i18n'
import { relTime } from '@/lib/relTime'

const props = defineProps<{
  projectId: string
  files: WorkspaceFile[]
  loading: boolean
  loadError: string
  /** 项目最近一次改动；项目还没有提交、或读不到时是 null。 */
  latest: ProjectChange | null
  query: string
  collapsedDirs: Set<string>
  viewing: WorkspaceFile | null
  change: ProjectChange | null
  read: ((asPdf: boolean) => Promise<ArrayBuffer>) | null
  wide: boolean
  historyOpen: boolean
  historyTitle: string
  history: ProjectChange[]
  historyLoading: boolean
  historyFailed: string | null
}>()

const emit = defineEmits<{
  'update:query': [value: string]
  retry: []
  select: [path: string]
  'toggle-dir': [path: string]
  /** 看改动记录：`null` 是整个项目的，给了路径就是那一份的。 */
  'show-history': [path: string | null]
  download: []
  'open-task': [taskId: string]
  'new-task': []
  'close-history': []
  'retry-history': []
}>()
</script>

<template>
  <AppPage
    :title="t('work.library.projectFiles.name')"
    :parent="{
      label: t('navigation.project.library'),
      to: { name: 'project-library', params: { projectId: props.projectId } },
    }"
    width="full"
    fill
  >
    <template v-if="props.latest && (props.wide || !props.viewing)" #meta>
      <span class="project-files-page__latest t-body">
        <v-icon icon="mdi-source-commit" size="18" class="c-faint" />
        <span class="c-muted">{{
          t(props.latest.task ? 'work.library.projectFiles.latestAccepted' : 'work.library.projectFiles.latestChanged')
        }}</span>
        <button
          v-if="props.latest.task"
          type="button"
          class="project-files-page__task"
          data-user-content
          @click="emit('open-task', props.latest.task.id)"
        >
          {{ t('work.library.projectFiles.taskTitle', { title: props.latest.task.title }) }}
        </button>
        <span v-if="props.wide || !props.latest.task" class="c-muted" data-user-content>{{ props.latest.author }}</span>
        <span class="c-muted">{{ relTime(props.latest.at) }}</span>
      </span>
      <BaseButton
        v-if="!props.wide"
        size="sm"
        icon="mdi-history"
        :aria-label="t('work.library.projectFiles.history')"
        @click="emit('show-history', null)"
      />
    </template>
    <template v-if="props.latest && props.wide" #controls>
      <BaseButton size="sm" prepend-icon="mdi-history" @click="emit('show-history', null)">
        {{ t('work.library.projectFiles.history') }}
      </BaseButton>
    </template>

    <div v-if="props.loading" class="py-8 text-center" role="status" :aria-label="t('work.library.loading')">
      <v-progress-circular indeterminate size="28" color="primary" />
    </div>
    <BaseLoadError
      v-else-if="props.loadError"
      class="ma-4"
      :title="t('work.library.projectFiles.loadError')"
      :error="props.loadError"
      @retry="emit('retry')"
    />
    <ProjectFilesBrowser
      v-else
      :query="props.query"
      :files="props.files"
      :collapsed-dirs="props.collapsedDirs"
      :viewing="props.viewing"
      :change="props.change"
      :read="props.read"
      :wide="props.wide"
      @update:query="(v: string) => emit('update:query', v)"
      @select="(path: string) => emit('select', path)"
      @toggle-dir="(path: string) => emit('toggle-dir', path)"
      @file-history="props.viewing && emit('show-history', props.viewing.path)"
      @download="emit('download')"
      @open-task="(id: string) => emit('open-task', id)"
      @new-task="emit('new-task')"
    />

    <ProjectChangesDialog
      :open="props.historyOpen"
      :title="props.historyTitle"
      :changes="props.history"
      :loading="props.historyLoading"
      :failed="props.historyFailed"
      @close="emit('close-history')"
      @retry="emit('retry-history')"
      @open-task="(id: string) => emit('open-task', id)"
    />
  </AppPage>
</template>

<style scoped>
.project-files-page__latest {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
}

.project-files-page__task {
  padding: 0;
  overflow: hidden;
  color: var(--accent-ink);
  text-overflow: ellipsis;
  cursor: pointer;
  background: none;
  border: 0;
  font: inherit;
}
</style>
