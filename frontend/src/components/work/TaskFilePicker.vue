<script setup lang="ts">
// 任务页「对话」那一种摆法里「文件」那一块标题栏左边那一段：此刻这一块显示的那份文件的路径（还没有
// 打开的是「文件」）。点开是一张小单子：这份文件所在目录里的文件和子目录（点目录进去、「上一级」
// 出来；还没打开文件时是任务的根目录），下面是这件任务里最近打开过的几份。挑一份，这一块就换成它。
import type { TaskFilesBundle } from '../../composables/useTaskFiles'

import { computed, ref, watch } from 'vue'

import FileKindMark from '../common/FileKindMark.vue'

import { t } from '@/i18n'

const props = defineProps<{ files: TaskFilesBundle }>()
const emit = defineEmits<{ pick: [path: string] }>()

const f = props.files
const parentOf = (path: string) => (path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '')
const nameOf = (path: string) => path.slice(path.lastIndexOf('/') + 1)

const open = ref(false)
/** 单子此刻列的是哪个目录（'' 是根目录）。 */
const dir = ref('')
watch(open, (now) => {
  if (now) dir.value = parentOf(f.openPath.value ?? '')
})

/** 这个目录里的子目录：列出来的目录，加上文件路径里出现过的。 */
const folders = computed<string[]>(() => {
  const here = dir.value
  const found = new Set<string>()
  const add = (path: string) => {
    if (here && !path.startsWith(here + '/')) return
    const rest = here ? path.slice(here.length + 1) : path
    const at = rest.indexOf('/')
    if (at > 0) found.add((here ? here + '/' : '') + rest.slice(0, at))
  }
  for (const folder of f.dirs.value) add(folder + '/')
  for (const file of f.files.value) add(file.path)
  return [...found].sort((a, b) => a.localeCompare(b))
})
const filesHere = computed<string[]>(() =>
  f.files.value
    .map((file) => file.path)
    .filter((path) => parentOf(path) === dir.value)
    .sort((a, b) => a.localeCompare(b))
)
/** 最近打开过的（「文件」打开过的页签），这个目录里已经列了的不再列。 */
const recent = computed<string[]>(() => f.tabs.value.filter((path) => parentOf(path) !== dir.value))

function pick(path: string) {
  open.value = false
  emit('pick', path)
}
</script>

<template>
  <v-menu v-model="open" location="bottom start" :close-on-content-click="false">
    <template #activator="{ props: menuProps }">
      <button
        type="button"
        class="file-pick"
        v-bind="menuProps"
        :title="t('work.room.taskFiles.switchFile')"
        data-file-pick
      >
        <span class="file-pick__path" :class="{ 'file-pick__path--code': !!f.openPath.value }">{{
          f.openPath.value ?? t('work.room.tabs.files')
        }}</span>
        <v-icon size="14" icon="mdi-chevron-down" />
      </button>
    </template>
    <v-list density="compact" class="file-pick__list" :aria-label="t('work.room.taskFiles.switchFile')">
      <v-list-item
        v-if="dir"
        prepend-icon="mdi-arrow-up"
        :title="t('work.room.taskFiles.up')"
        @click="dir = parentOf(dir)"
      />
      <v-list-item
        v-for="folder in folders"
        :key="folder"
        prepend-icon="mdi-folder-outline"
        :title="nameOf(folder)"
        @click="dir = folder"
      />
      <v-list-item
        v-for="path in filesHere"
        :key="path"
        :active="path === f.openPath.value"
        :title="nameOf(path)"
        @click="pick(path)"
      >
        <template #prepend><FileKindMark class="file-pick__mark" :name="path" /></template>
      </v-list-item>
      <template v-if="recent.length">
        <v-divider class="my-1" />
        <v-list-item
          v-for="path in recent"
          :key="path"
          :title="nameOf(path)"
          :subtitle="parentOf(path) || undefined"
          @click="pick(path)"
        >
          <template #prepend><FileKindMark class="file-pick__mark" :name="path" /></template>
        </v-list-item>
      </template>
    </v-list>
  </v-menu>
</template>

<style scoped>
.file-pick {
  display: inline-flex;
  flex: 0 1 auto;
  align-items: center;
  gap: 4px;
  min-width: 0;
  height: 26px;
  padding: 0 8px;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--ink);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.file-pick:hover {
  background: var(--fill-2);
}
.file-pick__path {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.file-pick__path--code {
  font-family: var(--font-mono);
  font-weight: 400;
}
.file-pick__list {
  min-width: 220px;
  max-height: 360px;
  overflow-y: auto;
}
.file-pick__mark {
  margin-right: 8px;
}
</style>
