<script setup lang="ts">
// 改动面板顶部那条横条：单独打开一份文件时才有。返回、这份文件、它的两面（差异 /
// 全文），以及对它做的事。
//
// 从 `PanelChangesView` 里分出来的（那个文件到了行数上限）：它管的是「打开着的那一份
// 文件」这一面，和下面的正文、和没打开文件时的全部改动面互不相干。事件原样往上传，
// 名字不变。
import type { FileSource, RoomTask } from '../../cx_types'
import type { FileDiff } from '../../lib/diff'

import { useDisplay } from 'vuetify'

import ChangesMoreMenu from './ChangesMoreMenu.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const props = defineProps<{
  openPath: string | null
  /** 这件任务的状态；结束了的任务还要说一句只读。 */
  sourceStatus: string
  fileDirty: boolean
  openDiff: FileDiff | null
  openIsDocument: boolean
  effectiveView: 'diff' | 'edit'
  fileReadOnly: boolean
  fileSaving: boolean
  currentTask: RoomTask | undefined
  fileSource: FileSource
  fileToolReady: boolean
  refreshing: boolean
}>()

const emit = defineEmits<{
  'close-file': []
  'view-changed': [view: 'diff' | 'edit']
  save: []
  'select-version': [source: FileSource]
  download: []
  refresh: []
}>()

const { mdAndUp } = useDisplay()
</script>

<template>
  <!-- The bar while one file is open: back, the file, its two faces (diff /
       full text), and the actions on it. The all-changes face has no bar; its
       top is the delivery's own state (the head the host slots in). -->
  <div v-if="props.openPath" class="changes-bar" :class="{ 'changes-bar--phone': !mdAndUp }">
    <span v-if="mdAndUp && props.sourceStatus" class="source-status">{{ props.sourceStatus }}</span>
    <span v-if="mdAndUp && props.sourceStatus" class="changes-bar__sep" aria-hidden="true" />
    <BaseButton
      kind="ghost"
      icon="mdi-arrow-left"
      size="sm"
      :class="{ 'tap-target': !mdAndUp }"
      :title="t('work.room.changes.backToAll')"
      :aria-label="t('work.room.changes.backToAll')"
      @click="emit('close-file')"
    />
    <span class="changes-bar__path" :title="props.openPath">
      {{ mdAndUp ? props.openPath : props.openPath.split('/').pop() }}
    </span>
    <span v-if="props.fileDirty" class="changes-bar__dot" :title="t('work.room.changes.unsaved')" />
    <v-spacer v-if="mdAndUp" />
    <!-- Diff / edit are two faces of the same file, and only a changed file has
         both. A document has neither: its diff is the one line "binary file
         differs", and editing it as text would corrupt it. -->
    <div v-if="props.openDiff && !props.openIsDocument" class="seg seg--sm">
      <button
        type="button"
        class="seg__btn"
        :class="{ 'seg__btn--on': props.effectiveView === 'diff' }"
        @click="emit('view-changed', 'diff')"
      >
        {{ t('work.room.changes.diffView') }}
      </button>
      <button
        type="button"
        class="seg__btn"
        :class="{ 'seg__btn--on': props.effectiveView === 'edit' }"
        @click="emit('view-changed', 'edit')"
      >
        {{ props.fileReadOnly || !mdAndUp ? t('work.room.changes.fullText') : t('work.room.changes.edit') }}
      </button>
    </div>
    <!-- Read-only files (binary / oversized / images) get no save button at
       all: saving one is what corrupted them. On a phone every file is
       read-only (see the CodeEditor branch), so there is no save either; one
       rule, not one per file. -->
    <span v-if="mdAndUp && props.fileReadOnly" class="changes-bar__ro">{{ t('work.room.changes.readOnly') }}</span>
    <BaseButton
      v-else-if="mdAndUp && props.effectiveView === 'edit'"
      kind="primary"
      size="sm"
      :loading="props.fileSaving"
      :disabled="!props.fileDirty"
      @click="emit('save')"
    >
      {{ t('work.room.changes.save') }}
    </BaseButton>
    <ChangesMoreMenu
      :current-task="props.currentTask"
      :file-source="props.fileSource"
      :can-download="props.fileToolReady"
      :refreshing="props.refreshing"
      @select-version="emit('select-version', $event)"
      @download="emit('download')"
      @refresh="emit('refresh')"
    />
  </div>
</template>

<style scoped>
.source-status {
  flex: 0 0 auto;
  white-space: nowrap;
  font-size: 13px;
  color: var(--muted);
  background: var(--fill);
  border-radius: var(--radius-sm);
  padding: 4px 8px;
}
.changes-bar {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 6px;
  min-width: 0;
  padding: 4px 8px;
  border-bottom: 1px solid var(--line);
}
.changes-bar__sep {
  flex: 0 0 auto;
  width: 1px;
  height: 16px;
  background: var(--line);
}
.changes-bar__path {
  min-width: 0;
  overflow: hidden;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.changes-bar__dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--muted);
  flex: 0 0 auto;
}
.changes-bar__ro {
  font-size: 12px;
  color: var(--muted);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  padding: 1px 6px;
  flex: 0 0 auto;
}
/* ⋯ 里「改动」那一项后面的计数：改过的文件有几个。 */
/* 分段开关 —— 下一张卡合并 Git 与 文件 时整块删掉。
   选中态靠「浮起来的一面」（surface 底 + 1px 描边 + ink 字重）而不是靠两档灰的
   明暗差：--fill 和 --surface 的明暗次序在两个主题之间是反的（浅色 surface #fff
   亮于 fill #f4f5f7，深色 surface #1b1d20 反而暗于 fill #212429），只差 6 级，
   深色下几乎看不出来 —— #526 修的侧栏选中态就是栽在这一条上。 */
.seg {
  display: inline-flex;
  gap: 2px;
}
.seg__btn {
  padding: 2px 12px;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--muted);
  font-size: 12px;
  cursor: pointer;
}
.seg__btn:hover {
  color: var(--ink);
}
.seg__btn--on {
  background: var(--surface);
  border-color: var(--line-2);
  color: var(--ink);
  font-weight: 600;
}
/* 手机上 360px 宽也要放下：← 路径 差异|全文 ⋯。让位的只有路径，其余不缩。 */
.changes-bar--phone {
  gap: 4px;
}
.changes-bar--phone .changes-bar__path {
  flex: 1 1 0;
}
.changes-bar--phone .seg {
  flex: none;
}
.changes-bar--phone .seg__btn {
  padding: 2px 8px;
  white-space: nowrap;
}
</style>
