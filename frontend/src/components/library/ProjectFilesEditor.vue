<script setup lang="ts">
// 资料库 › 项目文件在桌面上的样子：任务页「编辑器」那一种摆法的左栏和中间那一排页签，只读。
// 左栏是项目当前版本的目录树（任务编辑器的同一个 `TaskNavigator`，只给「文件」那一种），中间
// 一排页签是打开着的几份，下面是翻到的那一份（`FileBytesPreview`）。没有终端、对话和改动：
// 看不需要任务，要改的那一刻页头上的「编辑」才开一件任务。
//
// **只认 props**：取数、开着哪几份都在 `views/library/ProjectFiles.vue`。
import type { FileTreeSource } from '@/composables/useTaskFiles'
import type { ProjectChange, WorkspaceFile } from '@/cx_types'

import { computed, ref } from 'vue'

import { useTaskFileTree } from '@/composables/useTaskFileTree'

import BaseButton from '@/components/base/BaseButton.vue'
import FileBytesPreview from '@/components/common/FileBytesPreview.vue'
import { TASK_FILE_TREE_WIDTH } from '@/components/panels/taskFileTreeWidth'
import TaskNavigator from '@/components/panels/TaskNavigator.vue'
import EditorPart, { type EditorTab } from '@/components/work/EditorPart.vue'
import { t } from '@/i18n'
import { relTime } from '@/lib/relTime'

const props = defineProps<{
  files: WorkspaceFile[]
  /** 打开着的几份，按打开的先后。 */
  open: string[]
  /** 翻到的那一份；没有是 null。 */
  viewing: WorkspaceFile | null
  /** 翻到的那一份最后一次是怎么改的；还没读到、或读不到时是 null。 */
  change: ProjectChange | null
  read: ((asPdf: boolean) => Promise<ArrayBuffer>) | null
}>()

const emit = defineEmits<{
  select: [path: string]
  close: [path: string]
  'file-history': []
  download: []
  'open-task': [taskId: string]
}>()

const source: FileTreeSource = {
  files: computed(() => props.files),
  dirs: computed(() => []),
  touched: computed(() => new Set<string>()),
  openPath: computed(() => props.viewing?.path ?? null),
  arrange: async () => false,
}
const tree = useTaskFileTree(source)

const tabs = computed<EditorTab[]>(() =>
  props.open.map((path) => {
    const name = path.slice(path.lastIndexOf('/') + 1)
    return { key: path, title: name, hint: path, file: name, closable: true }
  })
)

const body = ref<HTMLElement | null>(null)
</script>

<template>
  <div class="project-files-editor" :style="{ '--tree-width': `${TASK_FILE_TREE_WIDTH.initial}px` }">
    <TaskNavigator
      class="project-files-editor__left"
      :files="source"
      :tree="tree"
      view="files"
      :views="['files']"
      read-only
      :can-split="false"
      @open-file="emit('select', $event)"
    />
    <EditorPart
      class="project-files-editor__centre"
      :tabs="tabs"
      :active="props.viewing?.path ?? null"
      :label="t('work.room.taskFiles.openFiles')"
      @select="emit('select', $event)"
      @close="emit('close', $event)"
      @body="body = $event"
    >
      <template v-if="props.viewing" #end>
        <BaseButton
          kind="ghost"
          size="sm"
          icon="mdi-history"
          :aria-label="t('work.library.projectFiles.history')"
          :title="t('work.library.projectFiles.history')"
          @click="emit('file-history')"
        />
        <BaseButton
          kind="ghost"
          size="sm"
          icon="mdi-download-outline"
          :aria-label="t('work.library.download')"
          :title="t('work.library.download')"
          @click="emit('download')"
        />
      </template>
    </EditorPart>
    <Teleport v-if="body" :to="body">
      <template v-if="props.viewing">
        <p v-if="props.change" class="t-meta c-muted project-files-editor__meta">
          <button
            v-if="props.change.task"
            type="button"
            class="project-files-editor__link"
            data-user-content
            @click="emit('open-task', props.change.task.id)"
          >
            {{ t('work.library.projectFiles.taskTitle', { title: props.change.task.title }) }}
          </button>
          <span data-user-content>{{ props.change.author }}</span>
          <span>{{ relTime(props.change.at) }}</span>
        </p>
        <div class="project-files-editor__preview">
          <FileBytesPreview
            v-if="props.read"
            :filename="props.viewing.path"
            :source="props.viewing.path"
            :read="props.read"
          />
        </div>
      </template>
      <p v-else class="t-body c-muted project-files-editor__empty">{{ t('work.room.taskFiles.noOpenFile') }}</p>
    </Teleport>
  </div>
</template>

<style scoped>
.project-files-editor {
  display: grid;
  grid-template-columns: var(--tree-width) minmax(0, 1fr);
  height: 100%;
  min-height: 0;
  border-top: 1px solid var(--line);
}

.project-files-editor__left {
  min-width: 0;
  border-right: 1px solid var(--line);
}

.project-files-editor__centre {
  min-width: 0;
}

.project-files-editor__meta {
  display: flex;
  flex: none;
  flex-wrap: wrap;
  column-gap: 8px;
  margin: 0;
  padding: 6px 16px;
  border-bottom: 1px solid var(--line);
}

.project-files-editor__link {
  padding: 0;
  color: var(--accent-ink);
  cursor: pointer;
  background: none;
  border: 0;
  font: inherit;
}

.project-files-editor__preview {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
}

.project-files-editor__empty {
  margin: auto;
}
</style>
