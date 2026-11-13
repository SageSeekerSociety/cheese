<script setup lang="ts">
// 资料库最上层排第一的那一行：项目文件，点进去是项目当前版本的目录树
// （`views/library/ProjectFilesView.vue`）。它不是资料库里的一个文件夹——里面的东西
// 不是人给进来的，没有替换、改名、删除，所以这一行没有 ⋯。
//
// 只在项目里有提交时才有这一行：页面先问一次最近的改动，问到了才画。
import type { ProjectChange } from '@/cx_types'

import { t } from '@/i18n'
import { relTime } from '@/lib/relTime'

const props = defineProps<{ latest: ProjectChange }>()
const emit = defineEmits<{ open: [] }>()
</script>

<template>
  <button type="button" class="project-files-entry" data-testid="library-project-files" @click="emit('open')">
    <v-icon icon="mdi-folder-home-outline" size="20" class="project-files-entry__icon" />
    <span class="project-files-entry__id">
      <span class="t-body project-files-entry__name">{{ t('work.library.projectFiles.name') }}</span>
      <span class="t-meta c-muted">{{
        t('work.library.projectFiles.updated', { when: relTime(props.latest.at) })
      }}</span>
    </span>
  </button>
</template>

<style scoped>
.project-files-entry {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  min-height: 56px;
  padding: 8px 12px;
  /* The list column spaces its blocks 12px apart; this row belongs with the rows under it, which sit 2px apart. */
  margin-bottom: -10px;
  color: var(--text);
  text-align: left;
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: var(--radius-md);
  transition: background-color var(--dur-quick) var(--ease-standard);
}

.project-files-entry:hover {
  background: var(--fill);
}

.project-files-entry__icon {
  flex: none;
  color: var(--accent-ink);
}

.project-files-entry__id {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.project-files-entry__name {
  color: var(--ink);
}
</style>
