<script setup lang="ts">
// 改动 tab: 这件任务干出来的东西，一个面看完。
//
// 它以前是两个半成品并排放着，中间一个分段开关：Git 那半是一坨没有语法着色、不能
// 按文件跳的裸 diff，文件那半是一棵不知道哪些文件被改过的树。想验收的人得先在
// 「改动」里读整块 diff 找出改了哪些文件，再切到「文件」里一个个翻出来看——两边
// 都不是一个能验收的面。
//
// 合成之后只有一棵树，标着每个文件改了多少。默认那一面把所有改动连着往下排（顶部是
// 宿主塞进来的这次交付的情况，`head` 插槽），点树上的文件就滚到它那一段；要看全文或
// 微调就点段头的「打开」，那一份单独开在这一格里（保存冲突的两条出路原样保留）。
// 不在改动里的文件从「打开其他文件」挑。
//
// **只认 props**：改过的文件、diff、当前打开哪一份、它读回来的正文，都是从外面
// 递进来的；点一份文件、按保存、切版本发事件出去。取数在
// `composables/usePanelChanges.ts`，`PanelChanges.vue` 那只薄容器把它接上——
// 于是这一格在测试和 /demo 里都只需要一串 props。
import type { DocumentRevisionsBundle } from '../../composables/useDocumentRevisions'
import type { FileSource, RoomTask, WorkspaceFile } from '../../cx_types'
import type { DiffLine, FileDiff } from '../../lib/diff'
import type { FileKind } from '../../lib/fileKind'
import type { MenuAction } from '../common/menuAction'

import { computed, nextTick, ref } from 'vue'
import { useDisplay } from 'vuetify'

import { buildFileRows, fmtBytes } from '../../lib/changesTree'
import CodeEditor from '../CodeEditor.vue'
import MobileActionSheet from '../common/MobileActionSheet.vue'

import PreviewPages from './preview/PreviewPages.vue'
import PreviewSheet from './preview/PreviewSheet.vue'
import RevisionList from './preview/RevisionList.vue'
import ChangesDiff from './ChangesDiff.vue'
import ChangesDiffList from './ChangesDiffList.vue'
import ChangesFileTree from './ChangesFileTree.vue'
import ChangesOpenFile from './ChangesOpenFile.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const props = defineProps<{
  topicId: string | null
  readOnly: boolean
  active?: boolean
  taskLoadError: string | null
  selectedTask: string | null
  currentTask: RoomTask | undefined
  /** 这件任务的状态；结束了的任务还要说一句只读。 */
  sourceStatus: string
  sourceUnavailable: boolean
  fileSource: FileSource
  fileToolReady: boolean
  loading: boolean
  refreshing: boolean
  errorMsg: string | null
  noRepo: boolean
  missing: string | null
  fileDiffs: FileDiff[]
  diffByPath: Map<string, FileDiff>
  /** 树上列的文件：这一支改到的那些。 */
  treeFiles: WorkspaceFile[]
  /** 「打开其他文件」从这里挑：这个来源里的全部文件。 */
  allFiles: WorkspaceFile[]
  openPath: string | null
  fileDraft: string
  fileSaving: boolean
  fileDirty: boolean
  fileVersion: string | null
  fileBinary: boolean
  fileTooLarge: boolean
  fileBytes: number
  fileReadOnly: boolean
  fileConflict: boolean
  openDiff: FileDiff | null
  openDiffLines: DiffLine[]
  effectiveView: 'diff' | 'edit'
  fileView: 'diff' | 'edit'
  openIsImage: boolean
  openIsDocument: boolean
  openDocumentType: FileKind | null
  /** 修订只长在 .docx 上，别的时候这里给的是 null。 */
  revisionPath: string | null
  openRawUrl: string
  collapsedDirs: Set<string>
  revealTick: number
  draftCount: number
  docBytes: ArrayBuffer | null
  docLoading: boolean
  docError: string
  docRendererMissing: boolean
  /** 这一份 .docx 的修订：清单、只读、处理动作都在里面（`useDocumentRevisions.ts`）。 */
  revs: DocumentRevisionsBundle
}>()

const emit = defineEmits<{
  (e: 'select-file', path: string): void
  /** 回到全部改动那一面。 */
  (e: 'close-file'): void
  (e: 'select-version', source: FileSource): void
  (e: 'toggle-dir', path: string): void
  (e: 'refresh'): void
  (e: 'download'): void
  (e: 'save'): void
  (e: 'overwrite'): void
  (e: 'reload'): void
  (e: 'view-changed', view: 'diff' | 'edit'): void
  (e: 'draft-changed', content: string): void
}>()

const { mdAndUp } = useDisplay()

// 手机上 ⋯ 是底部面板（同一组选项，桌面上仍是那个分了组的下拉菜单）。范围、版本
// 各是二选一，选中的那一项画成实心的圆。
const moreOpen = ref(false)
const moreActions = computed<MenuAction[]>(() => {
  const pick = (on: boolean) => (on ? 'mdi-radiobox-marked' : 'mdi-radiobox-blank')
  const item = (key: string, label: string, icon: string, onSelect: () => void): MenuAction => ({
    key,
    label,
    icon,
    onSelect,
  })
  const list: MenuAction[] = [
    item('open-other', t('work.room.changes.openOther'), 'mdi-file-search-outline', () => (pickerOpen.value = true)),
  ]
  if (props.currentTask?.status === 'open') {
    const live = props.fileSource === 'live'
    list.push(
      item('source-live', t('work.room.changes.liveFile'), pick(live), () => emit('select-version', 'live')),
      item('source-committed', t('work.room.changes.committedVersion'), pick(!live), () =>
        emit('select-version', 'committed')
      )
    )
  }
  if (props.fileToolReady && props.openPath) {
    list.push(item('download', t('work.room.changes.download'), 'mdi-download-outline', () => emit('download')))
  }
  list.push({
    ...item('refresh', t('work.room.changes.refresh'), 'mdi-refresh', () => emit('refresh')),
    loading: props.refreshing,
  })
  return list
})

// the ☰ toggle hides the list for a wider editor. 手机上一屏只放得下一样东西：列表默认
// 收着，打开时盖满这一格，点一份文件就收起来露出它。
const fileListOpen = ref(mdAndUp.value)

// 文件树那一列的宽度：拖它和编辑区之间那条线来改，记在这个浏览器里。路径长的仓库
// 一列 150px 只剩「old…」「wee…」，认不出是哪个文件。
const TREE_WIDTH_KEY = 'cheesex.changesTreeWidth'
const TREE_WIDTH = { min: 140, max: 520, initial: 220 }
function clampTreeWidth(px: number): number {
  return Math.round(Math.min(TREE_WIDTH.max, Math.max(TREE_WIDTH.min, px)))
}
function readTreeWidth(): number {
  try {
    const stored = Number(localStorage.getItem(TREE_WIDTH_KEY))
    return stored ? clampTreeWidth(stored) : TREE_WIDTH.initial
  } catch {
    return TREE_WIDTH.initial
  }
}
const treeWidth = ref(readTreeWidth())
function setTreeWidth(px: number) {
  treeWidth.value = clampTreeWidth(px)
  try {
    localStorage.setItem(TREE_WIDTH_KEY, String(treeWidth.value))
  } catch {
    // 存不下只是下次回到默认宽度。
  }
}
function startTreeDrag(e: MouseEvent) {
  const body = (e.currentTarget as HTMLElement).parentElement
  if (!body) return
  const left = body.getBoundingClientRect().left
  const move = (ev: MouseEvent) => setTreeWidth(ev.clientX - left)
  const stop = () => {
    window.removeEventListener('mousemove', move)
    window.removeEventListener('mouseup', stop)
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
  }
  window.addEventListener('mousemove', move)
  window.addEventListener('mouseup', stop)
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
}
// 树上点一个改过的文件：回到全部改动那一面，滚到它那一段。
const diffList = ref<InstanceType<typeof ChangesDiffList> | null>(null)
async function pickFile(path: string) {
  if (!mdAndUp.value) fileListOpen.value = false
  if (!props.diffByPath.has(path)) {
    emit('select-file', path)
    return
  }
  if (props.openPath) emit('close-file')
  await nextTick()
  diffList.value?.scrollTo(path)
}

const pickerOpen = ref(false)
function openOther(path: string) {
  pickerOpen.value = false
  emit('select-file', path)
}

// 全部改动那一面按树上的顺序排（文件夹在前、按名字），和左边那一列从上往下对得上；
// git 吐出来的顺序和树不一样，读的人会以为漏了文件。
const listDiffs = computed(() =>
  buildFileRows({ files: props.treeFiles, diffByPath: props.diffByPath, collapsedDirs: new Set() })
    .map((row) => (row.type === 'file' ? props.diffByPath.get(row.path) : undefined))
    .filter((d): d is FileDiff => !!d)
)

// 全部改动那一面的横条上说一共改了多少。
const totals = computed(() =>
  props.fileDiffs.reduce((acc, d) => ({ added: acc.added + d.added, removed: acc.removed + d.removed }), {
    added: 0,
    removed: 0,
  })
)

// 树上那些行：折成文件夹再摊平这件事在 lib/changesTree.ts（纯函数）。
const fileRows = computed(() =>
  buildFileRows({
    files: props.treeFiles,
    diffByPath: props.diffByPath,
    collapsedDirs: props.collapsedDirs,
  })
)
</script>

<template>
  <div class="panel-changes">
    <!-- 这一条就是这一格全部的横条：左边是这件任务的状态和打开的文件，右边是对这份
         文件做的事。偶尔才换的（范围、版本、下载、刷新）在 ⋯ 里。 -->
    <div class="changes-bar" :class="{ 'changes-bar--phone': !mdAndUp }">
      <span v-if="mdAndUp && props.sourceStatus" class="source-status">{{ props.sourceStatus }}</span>
      <template v-if="props.fileToolReady">
        <span v-if="mdAndUp" class="changes-bar__sep" aria-hidden="true" />
        <BaseButton
          kind="ghost"
          icon="mdi-format-list-bulleted"
          size="sm"
          class="file-icon-btn"
          :class="{ 'file-icon-btn--on': fileListOpen, 'tap-target': !mdAndUp }"
          :title="t('work.room.changes.fileList')"
          @click="fileListOpen = !fileListOpen"
        />
        <template v-if="props.openPath">
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
        </template>
        <span v-else class="changes-bar__summary">
          {{
            t('work.room.changes.summary', {
              count: props.fileDiffs.length,
              added: totals.added,
              removed: totals.removed,
            })
          }}
        </span>
      </template>
      <v-spacer v-if="mdAndUp || !props.fileToolReady" />
      <template v-if="props.fileToolReady">
        <!-- 看 diff / 改文件是同一个文件的两面，只有改过的文件才有两面。文档没有
             这两面：它的差异是一句「二进制文件不同」，而按文本编辑会损坏它。 -->
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
        <!-- Read-only files (binary / oversized / images) get no 保存 button at
           all: saving one is what corrupted them. 手机上文件只读（见 CodeEditor
           那一处），也就没有保存；每份都是只读，不必每份再说一次。 -->
        <span v-if="mdAndUp && props.fileReadOnly && props.openPath" class="changes-bar__ro">{{
          t('work.room.changes.readOnly')
        }}</span>
        <BaseButton
          v-else-if="mdAndUp && !props.fileReadOnly && props.effectiveView === 'edit'"
          kind="primary"
          size="sm"
          :loading="props.fileSaving"
          :disabled="!props.fileDirty"
          @click="emit('save')"
        >
          {{ t('work.room.changes.save') }}
        </BaseButton>
        <BaseButton
          v-if="mdAndUp && !props.openPath"
          kind="ghost"
          size="sm"
          prepend-icon="mdi-file-search-outline"
          @click="pickerOpen = true"
        >
          {{ t('work.room.changes.openOther') }}
        </BaseButton>
      </template>
      <template v-if="!mdAndUp">
        <BaseButton
          kind="ghost"
          icon="mdi-dots-horizontal"
          size="sm"
          class="tap-target"
          :title="t('work.room.changes.more')"
          :aria-label="t('work.room.changes.more')"
          :loading="props.refreshing"
          @click="moreOpen = true"
        />
        <MobileActionSheet v-model="moreOpen" :actions="moreActions" />
      </template>
      <v-menu v-else location="bottom end">
        <template #activator="{ props: menuProps }">
          <BaseButton
            v-bind="menuProps"
            kind="ghost"
            icon="mdi-dots-horizontal"
            size="sm"
            :title="t('work.room.changes.more')"
            :aria-label="t('work.room.changes.more')"
            :loading="props.refreshing"
          />
        </template>
        <v-list density="compact" :aria-label="t('work.room.changes.options')">
          <template v-if="props.currentTask?.status === 'open'">
            <v-list-subheader class="pt-0">{{ t('work.room.changes.version') }}</v-list-subheader>
            <v-list-item
              :title="t('work.room.changes.liveFile')"
              :subtitle="t('work.room.changes.liveNote')"
              :active="props.fileSource === 'live'"
              @click="emit('select-version', 'live')"
            />
            <v-list-item
              :title="t('work.room.changes.committedVersion')"
              :subtitle="t('work.room.changes.committedNote')"
              :active="props.fileSource === 'committed'"
              @click="emit('select-version', 'committed')"
            />
          </template>
          <v-divider class="my-1" />
          <v-list-item
            v-if="props.fileToolReady && props.openPath"
            :title="t('work.room.changes.download')"
            prepend-icon="mdi-download-outline"
            @click="emit('download')"
          />
          <v-list-item :title="t('work.room.changes.refresh')" prepend-icon="mdi-refresh" @click="emit('refresh')" />
        </v-list>
      </v-menu>
    </div>
    <v-alert v-if="props.taskLoadError" type="error" density="compact" class="ma-4">{{ props.taskLoadError }}</v-alert>
    <v-alert v-else-if="props.sourceUnavailable" type="warning" density="compact" class="ma-4">{{
      t('work.room.changes.sourceUnavailable')
    }}</v-alert>
    <template v-else>
      <!-- 转圈，不是骨架：这块地方长出来的是一套工具（150px 文件树 + 右边一格），
         而右边那一格可能是差异、编辑器、一张图，也可能是「只读 / 二进制」提示——
         等的是什么形状，这里并不知道。判据同 PanelPreview。 -->
      <div v-if="props.noRepo" class="changes-scroll">
        <slot name="head" />
        <p class="source-note">{{ t('work.room.changes.noRepo') }}</p>
      </div>
      <div v-else-if="props.loading" class="d-flex justify-center py-8">
        <v-progress-circular indeterminate color="primary" size="28" />
      </div>
      <v-alert v-else-if="props.errorMsg" type="error" density="compact" class="ma-4 file-load-error">
        {{ props.errorMsg }}
        <BaseButton
          v-if="props.fileSource === 'live'"
          kind="secondary"
          size="sm"
          @click="emit('select-version', 'committed')"
          >{{ t('work.room.changes.switchToCommitted') }}</BaseButton
        >
      </v-alert>

      <div v-else class="file-tool">
        <!-- chip 指来的文件不在这件任务里。列表照常显示：读者可以在树上挑别的文件。 -->
        <v-alert
          v-if="props.missing"
          type="info"
          variant="tonal"
          density="compact"
          class="ma-2"
          data-testid="missing-file"
        >
          {{ t('work.room.changes.missingInTask', { path: props.missing }) }}
        </v-alert>
        <!-- 保存冲突: 芝士 wrote this file after it was read. Show it and let the
           human choose — a silent winner is how edits vanished. -->
        <div v-if="props.fileConflict" class="file-conflict">
          <v-icon size="15" class="me-1">mdi-alert-outline</v-icon>
          <span class="file-conflict__text"> {{ t('work.room.changes.conflict') }} </span>
          <BaseButton kind="ghost" size="sm" @click="emit('reload')">{{
            t('work.room.changes.reloadLatest')
          }}</BaseButton>
          <BaseButton kind="danger" size="sm" :loading="props.fileSaving" @click="emit('overwrite')">
            {{ t('work.room.changes.saveAnyway') }}
          </BaseButton>
        </div>
        <div class="file-body" :class="{ 'file-body--phone': !mdAndUp }">
          <ChangesFileTree
            v-if="fileListOpen"
            :rows="fileRows"
            :collapsed-dirs="props.collapsedDirs"
            :active-path="props.openPath"
            :reveal-tick="props.revealTick"
            :cover="!mdAndUp"
            :width="treeWidth"
            :empty-label="t('work.room.changes.noChanges')"
            @select="pickFile"
            @toggle-dir="emit('toggle-dir', $event)"
          />
          <div
            v-if="fileListOpen && mdAndUp"
            class="tree-resizer"
            :title="t('work.topic.resize')"
            @mousedown.prevent="startTreeDrag"
            @dblclick="setTreeWidth(TREE_WIDTH.initial)"
          />
          <div class="file-editor">
            <!-- 文档：画出这一版，再把它自己带的修订列在旁边。排在差异前面，因为
               一份 .docx 的差异只有一句「二进制文件不同」。 -->
            <div v-if="props.openPath && props.openIsDocument" class="doc-view">
              <div v-if="props.docLoading && !props.docBytes" class="file-blob">
                <v-progress-circular indeterminate color="primary" size="24" />
              </div>
              <div v-else-if="props.docRendererMissing && !props.docBytes" class="file-blob">
                <v-icon size="30" class="c-faint mb-2">mdi-eye-off-outline</v-icon>
                <div class="file-blob__title">{{ t('work.room.changes.docPreviewDisabled') }}</div>
                <BaseButton kind="secondary" size="sm" class="mt-3" @click="emit('download')">
                  <v-icon size="16" class="me-1">mdi-download-outline</v-icon>
                  {{ t('work.room.changes.downloadOriginal') }}
                </BaseButton>
              </div>
              <div v-else-if="props.docError && !props.docBytes" class="file-blob">
                <v-icon size="30" class="text-warning mb-2">mdi-file-alert-outline</v-icon>
                <div class="file-blob__title">{{ t('work.room.changes.cantDisplay') }}</div>
                <div class="file-blob__note">{{ props.docError }}</div>
                <BaseButton kind="secondary" size="sm" class="mt-3" @click="emit('download')">
                  <v-icon size="16" class="me-1">mdi-download-outline</v-icon>
                  {{ t('work.room.changes.downloadOriginal') }}
                </BaseButton>
              </div>
              <div v-else class="doc-view__body">
                <PreviewPages v-if="props.openDocumentType?.view === 'pages'" :data="props.docBytes" />
                <PreviewSheet v-else :data="props.docBytes" :kind="props.openDocumentType?.sheet ?? 'workbook'" />
                <RevisionList :revs="props.revs" :path="props.revisionPath" />
              </div>
            </div>
            <!-- 逐文件 diff: 一个文件一段，增删各自着色。整块裸 diff 读不动，也没法
               定位到文件，所以验收动线以前根本立不起来。行号、折行、窗口化都在
               ChangesDiff 里。 -->
            <ChangesDiff v-else-if="props.openPath && props.effectiveView === 'diff'" :lines="props.openDiffLines" />
            <div v-else-if="props.openPath && props.openIsImage" class="file-image-view">
              <img :src="props.openRawUrl" :alt="props.openPath" />
            </div>
            <!-- Binary / oversized: no editor. Opening one in Monaco meant every
               byte utf-8 could not decode came back as U+FFFD, and 保存 wrote
               the damage to disk. -->
            <div v-else-if="props.openPath && (props.fileBinary || props.fileTooLarge)" class="file-blob">
              <v-icon size="30" class="c-faint mb-2">
                {{ props.fileTooLarge ? 'mdi-weight' : 'mdi-file-code-outline' }}
              </v-icon>
              <div class="file-blob__title">
                {{ props.fileTooLarge ? t('work.room.changes.tooLarge') : t('work.room.changes.binary') }}
              </div>
              <div class="file-blob__note">{{ props.openPath }} · {{ fmtBytes(props.fileBytes) }}</div>
              <BaseButton kind="secondary" size="sm" class="mt-3" @click="emit('download')">
                <v-icon size="16" class="me-1">mdi-download-outline</v-icon>
                {{ t('work.room.changes.downloadOriginal') }}
              </BaseButton>
            </div>
            <!-- 手机上只读：软键盘配 Monaco 不是能救的组合，给一个明确的说法比给一个
               难用的编辑器好。 -->
            <CodeEditor
              v-else-if="props.openPath"
              :model-value="props.fileDraft"
              :filename="props.openPath"
              :readonly="!mdAndUp || props.fileReadOnly"
              @update:model-value="emit('draft-changed', $event)"
              @save="emit('save')"
            />
            <!-- 没打开文件时是全部改动那一面：这次交付的情况在最上面（宿主塞进来），下面
                 每个改到的文件一段，连着往下排，一起滚。 -->
            <div v-else class="changes-scroll">
              <slot name="head" />
              <ChangesDiffList ref="diffList" :diffs="listDiffs" @open="emit('select-file', $event)" />
            </div>
          </div>
        </div>
      </div>
    </template>
    <p v-if="props.draftCount" class="source-note source-drafts">
      {{ t('work.room.changes.draftsKept') }}
    </p>
    <ChangesOpenFile v-model="pickerOpen" :files="props.allFiles" @pick="openOther" />
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
.source-note {
  margin: 0;
  padding: 12px 16px;
  font-size: 13px;
  color: var(--muted);
  overflow-wrap: anywhere;
}
.source-drafts {
  border-top: 1px solid var(--line);
}

.panel-changes {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  background: var(--surface);
}
.file-load-error {
  flex: 0 0 auto;
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
/* 没打开文件时右半边装的提交列表，也是这个 tab 唯一的另一个滚动层。 */
.changes-scroll {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
}
.changes-bar__summary {
  min-width: 0;
  overflow: hidden;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 树上的变更标记。增删各自用 wash 底 + ink 字：mark 色（--ok / --danger）当文字
   在浅色主题下读不到 4.5:1，而这两个数字是要被读的，不是被瞥见的。
   走 `:deep()` 是因为行上的标记由子组件（`ChangesFileTree`）画，父组件的 scoped
   选择器本来落不到它的内部元素上；这一条和它自己的那一份标记共用这一个定义。 */
:deep(.file-mark) {
  flex: 0 0 auto;
  margin-left: 4px;
  padding: 0 4px;
  border-radius: var(--radius-sm);
  font-size: 12px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
:deep(.file-mark--add) {
  color: var(--ok-ink);
  background: var(--ok-wash);
}
:deep(.file-mark--del) {
  color: var(--danger-ink);
  background: var(--danger-wash);
}

/* 逐文件 diff 的样式在 ChangesDiff.vue：渲染那一列的逻辑和它的长相都搬进了那个
   子组件（父组件的 scoped 选择器本来也落不到它内部）。 */

/* 文件: a two-pane browser — list + Monaco editor. Light, to match the app.
   Fills the tab height so the editor scrolls internally. */
.file-tool {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 0;
}
.file-conflict {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
  padding: 6px 8px;
  font-size: 13px;
  color: rgb(var(--v-theme-error));
  background: rgba(var(--v-theme-error), 0.07);
  border-bottom: 1px solid rgba(var(--v-theme-error), 0.25);
  flex: 0 0 auto;
}
.file-conflict__text {
  flex: 1 1 200px;
  min-width: 0;
}
.file-blob {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  padding: 16px;
  text-align: center;
}
.file-blob__title {
  font-size: 13px;
  color: var(--text);
}
.file-blob__note {
  font-size: 13px;
  color: var(--muted);
  margin-top: 4px;
  word-break: break-all;
}
.file-body {
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
}
/* 手机上文件列表盖满这一格：一份文件和一列文件名并排，两样都只剩半屏宽。 */
.file-body--phone {
  position: relative;
}
/* 手机上 360px 宽也要放下：← 来源 ☰ 路径 差异|全文 ⋯。让位的只有路径，其余不缩。 */
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
/* 文件树和编辑区之间那条线：看得见的 1px，能抓的左右各多 4px（和对话、面板之间那条
   同一种画法）。 */
.tree-resizer {
  position: relative;
  z-index: var(--z-raised);
  flex: 0 0 1px;
  margin-left: -1px;
  cursor: col-resize;
  background: transparent;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.tree-resizer::before {
  content: '';
  position: absolute;
  inset: 0 -4px;
}
.tree-resizer:hover {
  background: var(--faint);
}
.file-editor {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  background: var(--surface);
}
/* 文档那一面：页面在左，修订柱在右，和预览那一格同一个排法。 */
.doc-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}
.doc-view__body {
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
  min-width: 0;
}
@media (max-width: 720px) {
  .doc-view__body {
    flex-direction: column;
  }
}
.file-icon-btn--on :deep(.v-icon) {
  color: rgb(var(--v-theme-primary));
}
.file-image-view {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  overflow: auto;
  background: conic-gradient(var(--line-2) 0 25%, transparent 0 50%, var(--line-2) 0 75%, transparent 0) 0 0 / 16px 16px; /* checkerboard so transparency reads */
}
.file-image-view img {
  max-width: 95%;
  max-height: 95%;
  object-fit: contain;
  box-shadow: var(--shadow-1);
  /* The container's checkerboard is what says "transparent"; the image itself
     sits on the panel surface so a PNG with alpha is not slammed onto a white
     slab in the dark theme (GitHub's image viewer does the same). */
  background: var(--surface);
}
</style>
