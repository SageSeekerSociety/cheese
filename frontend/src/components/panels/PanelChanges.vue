<script setup lang="ts">
// 任务页右侧「改动」这一格 —— 一只薄容器。
//
// 它以前是一个 1709 行的组件：自己引十个接口函数、自己按 20 秒轮询、自己挂
// `beforeunload`、自己读共享草稿，然后又自己把那一切画出来。于是「改动」这个界面
// 在测试和 /demo 里都必须先立一个假后端（`views/demo/demoPanels.ts` 最初就是为它
// 写的十几条路由）。
//
// 现在两边分家，和 #2118 拆 UserRef 是同一个形状：
//   - 取数（接口、轮询、读到写、草稿、冲突）
//     → `composables/usePanelChanges.ts`
//   - 画（树上标了什么、diff 什么颜色、空态写哪句话）
//     → `components/panels/PanelChangesView.vue`，只凭 props 渲染
//
// 取数那一层由 `components/work/PanelChangesHost.vue` 调（这一格在
// `components/panels/` 下，是场景棘轮里的一个「场景」，场景不取数、也不引会取数的
// 模块），结果整包从这里递下去。这一只只负责把两边接起来：状态递下去、动作接
// 回来。加取数动作在组合式函数里加，加画法在展示组件里加，这一只基本不再长。
import type { PanelChangesBundle } from '../../composables/usePanelChanges'
import type { FileSource } from '../../cx_types'

import PanelChangesView from './PanelChangesView.vue'

const props = withDefaults(
  defineProps<{
    topicId: string | null
    readOnly?: boolean
    /** 这一格的取数（`composables/usePanelChanges.ts` 那一包）。 */
    changes: PanelChangesBundle
  }>(),
  { readOnly: false }
)

const {
  taskLoadError,
  selectedTask,
  currentTask,
  sourceStatus,
  sourceUnavailable,
  showAll,
  fileSource,
  fileToolReady,
  loading,
  refreshing,
  errorMsg,
  noRepo,
  missing,
  gitCommits,
  fileDiffs,
  diffByPath,
  treeFiles,
  openPath,
  fileDraft,
  fileSaving,
  fileDirty,
  fileVersion,
  fileBinary,
  fileTooLarge,
  fileBytes,
  fileReadOnly,
  fileConflict,
  openDiff,
  openDiffLines,
  effectiveView,
  fileView,
  openIsImage,
  openIsDocument,
  openDocumentType,
  revisionPath,
  openRawUrl,
  expandedDirs,
  revealTick,
  draftCount,
  docBytes,
  docLoading,
  docError,
  docRendererMissing,
  revs,
  // 动作
  loadAll,
  selectFile,
  selectVersion,
  openFile,
  toggleDir,
  downloadOpenFile,
  saveFile,
  overwriteFile,
  reloadOpenFile,
} = props.changes

// 展示组件往上发的三件事是「换了个值」，不是「做了个动作」：这里落回取数那一层那
// 几个 ref 上。写成三个函数而不是模板里的行内赋值，是为了让类型检查看得见。
function setScope(v: boolean) {
  showAll.value = v
}
function setView(v: 'diff' | 'edit') {
  fileView.value = v
}
function setDraft(v: string) {
  fileDraft.value = v
}
// ⋯ 里的刷新和首屏那次加载走同一条路，只是不转圈。
function refresh() {
  void loadAll({ silent: true })
}
function onSelectFile(path: string) {
  void selectFile(path)
}
function onSelectVersion(source: FileSource) {
  void selectVersion(source)
}
function onDownload() {
  void downloadOpenFile()
}

// 地址里的 chip 指到某个文件时，工作面板会拿着文件路径来开这一格。
defineExpose({ openFile })
</script>

<template>
  <!-- 一次 props 面摊开，而不是 v-bind 一整包：这三十来样东西就是这一格的接口，
       谁传谁看得见；将来哪一样不传了，typecheck 也会点名。 -->
  <PanelChangesView
    :topic-id="props.topicId"
    :read-only="props.readOnly"
    :task-load-error="taskLoadError"
    :selected-task="selectedTask"
    :current-task="currentTask"
    :source-status="sourceStatus"
    :source-unavailable="sourceUnavailable"
    :show-all="showAll"
    :file-source="fileSource"
    :file-tool-ready="fileToolReady"
    :loading="loading"
    :refreshing="refreshing"
    :error-msg="errorMsg"
    :no-repo="noRepo"
    :missing="missing"
    :git-commits="gitCommits"
    :file-diffs="fileDiffs"
    :diff-by-path="diffByPath"
    :tree-files="treeFiles"
    :open-path="openPath"
    :file-draft="fileDraft"
    :file-saving="fileSaving"
    :file-dirty="fileDirty"
    :file-version="fileVersion"
    :file-binary="fileBinary"
    :file-too-large="fileTooLarge"
    :file-bytes="fileBytes"
    :file-read-only="fileReadOnly"
    :file-conflict="fileConflict"
    :open-diff="openDiff"
    :open-diff-lines="openDiffLines"
    :effective-view="effectiveView"
    :file-view="fileView"
    :open-is-image="openIsImage"
    :open-is-document="openIsDocument"
    :open-document-type="openDocumentType"
    :revision-path="revisionPath"
    :revs="revs"
    :open-raw-url="openRawUrl"
    :expanded-dirs="expandedDirs"
    :reveal-tick="revealTick"
    :draft-count="draftCount"
    :doc-bytes="docBytes"
    :doc-loading="docLoading"
    :doc-error="docError"
    :doc-renderer-missing="docRendererMissing"
    @select-file="onSelectFile"
    @select-version="onSelectVersion"
    @toggle-dir="toggleDir"
    @refresh="refresh"
    @download="onDownload"
    @save="saveFile"
    @overwrite="overwriteFile"
    @reload="reloadOpenFile"
    @scope-changed="setScope"
    @view-changed="setView"
    @draft-changed="setDraft"
  />
</template>
