<script setup lang="ts">
// 资料库里的「项目文件」：项目当前版本的文件，左边一棵目录树，右边看一份。
//
// 它和资料库的列表不是一种东西：资料库是一份份给进来的原件，这里是一整个项目——目录
// 有意义，文件一起变，人想知道的是「项目现在什么样、最近改了什么」。所以这里没有类型
// 筛选、排序、来源，也不能上传、替换、改名；要改就新建任务。
//
// **只认 props**：取数、地址上开着哪一份、改动记录都在 `views/library/ProjectFilesView.vue`。
// 树怎么折在 `lib/changesTree.ts`（任务「改动」那棵树用的同一份）；这里只画行。
import type { ProjectChange, WorkspaceFile } from '@/cx_types'

import { computed } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import FileBytesPreview from '@/components/common/FileBytesPreview.vue'
import { t } from '@/i18n'
import { buildFileRows } from '@/lib/changesTree'
import { KIND_ICONS, kindOf } from '@/lib/libraryKinds'
import { relTime } from '@/lib/relTime'

const props = defineProps<{
  files: WorkspaceFile[]
  query: string
  /** 收起的文件夹。 */
  collapsedDirs: Set<string>
  /** 右边开着的那一份；没有是 null。 */
  viewing: WorkspaceFile | null
  /** 这一份最后一次是怎么改的；还没读到、或读不到时是 null。 */
  change: ProjectChange | null
  read: ((asPdf: boolean) => Promise<ArrayBuffer>) | null
  /** 桌面（树和文件并排）还是手机（一次一页）。 */
  wide: boolean
}>()

const emit = defineEmits<{
  'update:query': [value: string]
  select: [path: string]
  'toggle-dir': [path: string]
  'file-history': []
  download: []
  'open-task': [taskId: string]
  'new-task': []
}>()

const searching = computed(() => !!props.query.trim())
/** 一搜就平铺：带完整路径的那几份，不再按目录折。 */
const rows = computed(() => {
  const q = props.query.trim().toLowerCase()
  if (q) {
    return props.files
      .filter((f) => f.path.toLowerCase().includes(q))
      .map((f) => ({ type: 'file' as const, path: f.path, name: f.path, depth: 0 }))
  }
  return buildFileRows({ files: props.files, diffByPath: new Map(), collapsedDirs: props.collapsedDirs })
})

const treeShown = computed(() => props.wide || !props.viewing)
// 文档、PDF 排成一页（留边、限宽）；代码、表格、图片贴边。
const prose = computed(() => {
  const kind = props.viewing ? kindOf(props.viewing.path) : 'other'
  return kind === 'doc' || kind === 'pdf'
})
</script>

<template>
  <div class="project-files" :class="{ 'project-files--phone': !props.wide }">
    <nav v-if="treeShown" class="project-files__tree" :aria-label="t('work.library.projectFiles.name')">
      <div class="project-files__search">
        <v-text-field
          :model-value="props.query"
          type="search"
          autocomplete="off"
          variant="outlined"
          density="compact"
          prepend-inner-icon="mdi-magnify"
          :placeholder="t('work.library.projectFiles.search')"
          :aria-label="t('work.library.projectFiles.search')"
          hide-details
          @update:model-value="(v: string) => emit('update:query', v ?? '')"
        />
      </div>
      <ul class="project-files__rows">
        <li v-for="row in rows" :key="`${row.type}:${row.path}`">
          <button
            type="button"
            class="project-files__row t-body"
            :class="{ 'project-files__row--on': row.type === 'file' && props.viewing?.path === row.path }"
            :style="{ paddingLeft: `${10 + row.depth * 16}px` }"
            :title="row.path"
            :aria-expanded="row.type === 'dir' ? !props.collapsedDirs.has(row.path) : undefined"
            @click="row.type === 'dir' ? emit('toggle-dir', row.path) : emit('select', row.path)"
          >
            <v-icon
              v-if="row.type === 'dir'"
              :icon="props.collapsedDirs.has(row.path) ? 'mdi-chevron-right' : 'mdi-chevron-down'"
              size="16"
              class="c-faint"
            />
            <span v-else-if="!searching" class="project-files__gap" />
            <v-icon
              :icon="row.type === 'dir' ? 'mdi-folder-outline' : KIND_ICONS[kindOf(row.path)]"
              size="18"
              class="project-files__icon"
            />
            <span class="project-files__name" data-user-content>{{ row.name }}</span>
          </button>
        </li>
      </ul>
      <p v-if="!rows.length" class="t-body c-muted project-files__empty">
        {{ t(searching ? 'work.library.projectFiles.noMatch' : 'work.library.projectFiles.empty') }}
      </p>
      <p class="t-meta c-muted project-files__note">
        {{ t('work.library.projectFiles.note') }}
        <button type="button" class="project-files__link" @click="emit('new-task')">
          {{ t('work.library.projectFiles.newTask') }}
        </button>
      </p>
    </nav>

    <section v-if="props.viewing" class="project-files__view">
      <header class="project-files__head">
        <div class="project-files__head-row">
          <h2 v-if="props.wide" class="t-title project-files__path" data-user-content>{{ props.viewing.path }}</h2>
          <div v-if="props.wide" class="project-files__actions">
            <BaseButton size="sm" prepend-icon="mdi-history" @click="emit('file-history')">
              {{ t('work.library.projectFiles.history') }}
            </BaseButton>
            <BaseButton kind="primary" size="sm" prepend-icon="mdi-download-outline" @click="emit('download')">
              {{ t('work.library.download') }}
            </BaseButton>
          </div>
        </div>
        <p v-if="props.change" class="t-meta c-muted project-files__meta">
          <button
            v-if="props.change.task"
            type="button"
            class="project-files__link"
            data-user-content
            @click="emit('open-task', props.change.task.id)"
          >
            {{ t('work.library.projectFiles.taskTitle', { title: props.change.task.title }) }}
          </button>
          <span data-user-content>{{ props.change.author }}</span>
          <span>{{ relTime(props.change.at) }}</span>
        </p>
      </header>
      <div class="project-files__preview" :class="{ 'project-files__preview--prose': prose }">
        <div class="project-files__page">
          <FileBytesPreview
            v-if="props.read"
            :filename="props.viewing.path"
            :source="props.viewing.path"
            :read="props.read"
          />
        </div>
      </div>
      <div v-if="!props.wide" class="project-files__bar">
        <BaseButton kind="primary" block @click="emit('download')">{{ t('work.library.download') }}</BaseButton>
        <BaseButton block prepend-icon="mdi-history" @click="emit('file-history')">
          {{ t('work.library.projectFiles.history') }}
        </BaseButton>
      </div>
    </section>
  </div>
</template>

<style scoped>
.project-files {
  display: flex;
  height: 100%;
  min-height: 0;
}

.project-files__tree {
  display: flex;
  flex: none;
  flex-direction: column;
  width: 280px;
  min-height: 0;
  border-right: 1px solid var(--line);
}

.project-files--phone .project-files__tree {
  flex: 1 1 auto;
  width: 100%;
  border-right: 0;
}

.project-files__search {
  flex: none;
  padding: 10px 10px 6px;
}

.project-files__rows {
  flex: 1 1 auto;
  min-height: 0;
  margin: 0;
  padding: 0 6px 8px;
  overflow-y: auto;
  list-style: none;
}

.project-files__row {
  display: flex;
  align-items: center;
  gap: 4px;
  width: 100%;
  min-height: 32px;
  padding-right: 8px;
  color: var(--text);
  text-align: left;
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: var(--radius-sm);
  transition: background-color var(--dur-quick) var(--ease-standard);
}

.project-files--phone .project-files__row {
  min-height: 44px;
}

.project-files__row:hover {
  background: var(--fill);
}

.project-files__row--on {
  color: var(--ink);
  font-weight: 600;
  background: var(--fill);
}

.project-files__gap {
  display: inline-block;
  flex: none;
  width: 16px;
}

.project-files__icon {
  flex: none;
  color: var(--muted);
}

.project-files__name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.project-files__empty {
  margin: 0;
  padding: 8px 16px;
}

.project-files__note {
  flex: none;
  margin: 0;
  padding: 10px 14px calc(10px + env(safe-area-inset-bottom));
  border-top: 1px solid var(--line);
}

.project-files__link {
  padding: 0;
  color: var(--accent-ink);
  cursor: pointer;
  background: none;
  border: 0;
  font: inherit;
}

.project-files__view {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}

.project-files__head {
  display: flex;
  flex: none;
  flex-direction: column;
  gap: 2px;
  padding: 10px 16px 10px 24px;
  border-bottom: 1px solid var(--line);
}

.project-files--phone .project-files__head {
  padding: 8px 16px;
}

.project-files__head-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.project-files__path {
  flex: 1 1 auto;
  min-width: 0;
  margin: 0;
  overflow-wrap: anywhere;
}

.project-files__actions {
  display: flex;
  flex: none;
  gap: 6px;
}

.project-files__meta {
  display: flex;
  flex-wrap: wrap;
  column-gap: 8px;
  margin: 0;
}

.project-files__preview {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
}

.project-files__preview--prose .project-files__page {
  max-width: 820px;
  margin: 0 auto;
  padding: 32px 40px 64px;
}

.project-files--phone .project-files__preview--prose .project-files__page {
  padding: 16px 16px 32px;
}

.project-files__bar {
  display: grid;
  flex: none;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  padding: 10px 16px calc(10px + env(safe-area-inset-bottom));
  border-top: 1px solid var(--line);
}
</style>
