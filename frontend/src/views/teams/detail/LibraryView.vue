<script setup lang="ts">
// 团队的资料库，一个人自己的就是他名下那个团队的：团队自己的文档和文件在一张表里，
// 没有「在哪个频道给的」。画面和项目资料库是同一套行、同一栏详情、同一个版本框；取数、
// 新建文档、上传、替换、挪动、删除都在页面（`Library.vue`），这里只摆、只报动作。
import type { ListedDocument } from '@/api/projectDocuments'
import type { LibraryRow } from '@/composables/useLibraryPages'
import type { ProjectMemberRow } from '@/cx_types'
import type { LibraryFile, LibraryFolder, LibraryVersion } from '@/lib/libraryApi'
import type { Kind } from '@/lib/libraryKinds'

import { computed, ref } from 'vue'

import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import ConfirmDialog from '@/components/base/ConfirmDialog.vue'
import { t } from '@/i18n'
import { fmtBytes } from '@/lib/changesTree'
import { leafOf } from '@/lib/libraryFolders'
import { kindLabel, KINDS } from '@/lib/libraryKinds'
import LibraryCrumbs from '@/views/library/LibraryCrumbs.vue'
import LibraryFileDetail from '@/views/library/LibraryFileDetail.vue'
import LibraryNameDialog from '@/views/library/LibraryNameDialog.vue'
import LibraryRows from '@/views/library/LibraryRows.vue'
import LibraryVersionsDialog from '@/views/library/LibraryVersionsDialog.vue'

const props = defineProps<{
  team: number
  /** 一个人自己的资料库：开头那句话说的是他和芝士之间的文件。 */
  personal: boolean
  /** 占了多少、一共多少（字节）；还没取到是 null。 */
  room: { used: number; total: number } | null
  rows: LibraryRow[]
  /** 文档行上写谁改的，用成员的名字。 */
  members: ProjectMemberRow[]
  /** 正在问要不要删的那份文档。 */
  confirmingDoc: ListedDocument | null
  loading: boolean
  loadFailed: boolean
  loadReason: string
  actionError: string
  dir: string
  /** 一层一层地看（不搜不筛）。 */
  browsing: boolean
  /** 最上层什么都没有、又没在找：搜索框和筛选不摆。 */
  anything: boolean
  selected: LibraryFile | null
  selectedPath: string
  busy: string
  wide: boolean
  revision: number
  read: (asPdf: boolean) => Promise<ArrayBuffer>
  versionsOf: string | null
  versions: LibraryVersion[]
  versionsLoading: boolean
  versionsFailed: string | null
  versionsError: string
  restoring: string | null
  replacing: { target: LibraryFile; file: File } | null
  confirming: LibraryFile | LibraryFolder | null
  naming: boolean
  /** 名字框打开时填好的样子（`useLibraryNaming` 的 `dialog`）。 */
  namingDialog: { title: string; primaryLabel: string; name?: string; folder?: string; folders?: string[] }
  namingBusy: boolean
  namingError: string
}>()

const query = defineModel<string>('query', { required: true })
const kind = defineModel<Kind>('kind', { required: true })

const emit = defineEmits<{
  go: [dir: string]
  open: [file: LibraryFile]
  close: []
  openDoc: [id: string]
  removeDoc: [doc: ListedDocument]
  confirmRemoveDoc: []
  cancelRemoveDoc: []
  upload: [files: File[]]
  download: [file: LibraryFile]
  replacement: [target: LibraryFile, file: File]
  confirmReplace: []
  cancelReplace: []
  rename: [path: string]
  versions: [file: LibraryFile]
  closeVersions: []
  retryVersions: []
  downloadVersion: [version: LibraryVersion]
  restoreVersion: [version: LibraryVersion]
  remove: [target: LibraryFile | LibraryFolder]
  confirmRemove: []
  cancelRemove: []
  closeNaming: []
  submitNaming: [to: string]
  clearFilters: []
  retry: []
}>()

const listEl = ref<HTMLElement | null>(null)
const picker = ref<HTMLInputElement | null>(null)
const replacePicker = ref<HTMLInputElement | null>(null)
const dragging = ref(false)
let replaceTarget: LibraryFile | null = null

const inFolder = computed(() => props.browsing && !!props.dir)

function picked(event: Event) {
  const input = event.target as HTMLInputElement
  emit('upload', Array.from(input.files ?? []))
  input.value = ''
}

function dropped(event: DragEvent) {
  dragging.value = false
  emit('upload', Array.from(event.dataTransfer?.files ?? []))
}

function pickReplacement(file: LibraryFile) {
  replaceTarget = file
  replacePicker.value?.click()
}

function replacementPicked(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (file && replaceTarget) emit('replacement', replaceTarget, file)
}

defineExpose({
  listEl,
  /** 页头上的「上传文件」：打开选文件的框。 */
  pickFiles: () => picker.value?.click(),
})
</script>

<template>
  <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- a drop target for files dragged in; the upload action is the keyboard's way -->
  <div
    class="team-library"
    :class="{ 'team-library--dragging': dragging }"
    @dragover.prevent="dragging = true"
    @dragleave.self="dragging = false"
    @drop.prevent="dropped"
  >
    <input ref="picker" type="file" multiple hidden :aria-label="t('work.library.upload')" @change="picked" />
    <input ref="replacePicker" type="file" hidden :aria-label="t('work.library.replace')" @change="replacementPicked" />

    <section v-if="wide || !selected" ref="listEl" class="team-library__list">
      <p class="t-body c-muted team-library__intro" :title="t('work.library.dropHint')">
        {{ personal ? t('teams.library.introPersonal') : t('teams.library.intro') }}
      </p>
      <div v-if="room" class="team-library__room">
        <div
          class="team-library__meter"
          role="meter"
          :aria-valuenow="room.used"
          :aria-valuemin="0"
          :aria-valuemax="room.total"
          :aria-label="t('teams.library.room', { used: fmtBytes(room.used), total: fmtBytes(room.total) })"
        >
          <i :style="{ width: `${Math.min(100, (room.used / room.total) * 100)}%` }" />
        </div>
        <span class="t-meta c-muted">
          {{ t('teams.library.room', { used: fmtBytes(room.used), total: fmtBytes(room.total) }) }}
        </span>
      </div>

      <BaseLoadError
        v-if="loadFailed"
        :title="t('work.library.loadError')"
        :error="loadReason || null"
        @retry="emit('retry')"
      />
      <p v-if="actionError" role="alert" class="t-body c-danger">{{ actionError }}</p>

      <div v-if="anything" class="team-library__tools">
        <v-text-field
          v-model="query"
          type="search"
          autocomplete="off"
          variant="outlined"
          density="compact"
          prepend-inner-icon="mdi-magnify"
          :placeholder="t('work.library.search')"
          :aria-label="t('work.library.search')"
          hide-details
          class="team-library__search"
        />
        <div class="team-library__kinds" role="group" :aria-label="t('work.library.kindsLabel')">
          <button
            v-for="k in KINDS"
            :key="k"
            type="button"
            class="team-library__kind t-meta"
            :aria-pressed="kind === k"
            @click="kind = k"
          >
            {{ kindLabel(k) }}
          </button>
        </div>
      </div>

      <LibraryCrumbs v-if="inFolder" :dir="dir" :root="t('home.nav.teamLibrary')" @go="(d: string) => emit('go', d)" />

      <div
        v-if="loading && !rows.length"
        class="py-8 text-center"
        role="status"
        :aria-label="t('work.library.loading')"
      >
        <v-progress-circular indeterminate size="28" color="primary" />
      </div>

      <LibraryRows
        v-if="rows.length"
        :rows="rows"
        :scroller="listEl"
        :selected-path="selectedPath"
        :busy="busy"
        :browsing="browsing"
        :members="members"
        :agent-name="t('work.room.defaultAgentName')"
        @open-file="(file: LibraryFile) => emit('open', file)"
        @open-folder="(d: string) => emit('go', d)"
        @open-doc="(id: string) => emit('openDoc', id)"
        @delete-doc="(doc: ListedDocument) => emit('removeDoc', doc)"
        @download="(file: LibraryFile) => emit('download', file)"
        @replace="pickReplacement"
        @rename="(path: string) => emit('rename', path)"
        @versions="(file: LibraryFile) => emit('versions', file)"
        @delete="(target: LibraryFile | LibraryFolder) => emit('remove', target)"
      />
      <BaseEmptyState
        v-else-if="inFolder && !loading"
        size="inline"
        align="center"
        class="team-library__empty"
        :title="t('work.library.folderEmpty')"
      />
      <BaseEmptyState
        v-else-if="!browsing && !loading"
        size="inline"
        align="center"
        class="team-library__empty"
        :title="t('work.library.noMatch')"
        :action="t('work.library.clearFilters')"
        @action="emit('clearFilters')"
      />
      <BaseEmptyState
        v-else-if="!loadFailed && !loading"
        size="inline"
        align="center"
        class="team-library__empty"
        :title="t('work.library.empty')"
      />
    </section>

    <LibraryFileDetail
      v-if="selected"
      :shelf="{ team }"
      :file="selected"
      :wide="wide"
      :revision="revision"
      :busy="busy === selected.path"
      :read="read"
      @download="emit('download', selected)"
      @replace="pickReplacement(selected)"
      @close="emit('close')"
    />

    <LibraryVersionsDialog
      :path="versionsOf"
      :versions="versions"
      :loading="versionsLoading"
      :failed="versionsFailed"
      :action-error="versionsError"
      :restoring="restoring"
      :can-restore="true"
      :fmt-bytes="fmtBytes"
      @close="emit('closeVersions')"
      @retry="emit('retryVersions')"
      @download="(v: LibraryVersion) => emit('downloadVersion', v)"
      @restore="(v: LibraryVersion) => emit('restoreVersion', v)"
    />

    <ConfirmDialog
      :model-value="!!replacing"
      :title="t('work.library.replaceTitle', { name: replacing?.target.path ?? '' })"
      :confirm-label="t('work.library.replaceConfirm')"
      @update:model-value="emit('cancelReplace')"
      @confirm="emit('confirmReplace')"
    >
      {{ t('work.library.replaceBody', { file: replacing?.file.name ?? '' }) }}
    </ConfirmDialog>

    <ConfirmDialog
      :model-value="!!confirming"
      :title="t('work.library.deleteTitle', { name: confirming ? leafOf(confirming.path) : '' })"
      :confirm-label="t('work.library.delete')"
      danger
      @update:model-value="emit('cancelRemove')"
      @confirm="emit('confirmRemove')"
    >
      {{
        confirming && 'count' in confirming
          ? t('work.library.deleteFolderBody', { n: confirming.count })
          : t('work.library.deleteBody')
      }}
    </ConfirmDialog>

    <ConfirmDialog
      :model-value="!!confirmingDoc"
      :title="t('work.library.deleteTitle', { name: confirmingDoc?.title || t('work.room.doc.untitled') })"
      :confirm-label="t('work.library.delete')"
      danger
      @update:model-value="emit('cancelRemoveDoc')"
      @confirm="emit('confirmRemoveDoc')"
    >
      {{ t('work.library.deleteDocBody') }}
    </ConfirmDialog>

    <LibraryNameDialog
      :model-value="naming"
      v-bind="namingDialog"
      :loading="namingBusy"
      :error="namingError"
      @update:model-value="emit('closeNaming')"
      @submit="(to: string) => emit('submitNaming', to)"
    />
  </div>
</template>

<style scoped>
.team-library {
  display: flex;
  height: 100%;
  min-height: 0;
  outline: 2px dashed transparent;
  outline-offset: -8px;
  transition: outline-color var(--dur-quick) var(--ease-standard);
}

.team-library--dragging {
  outline-color: var(--accent);
}

.team-library__list {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
  padding: 20px 24px 32px;
  overflow-y: auto;
}

.team-library__intro {
  margin: 0;
}

.team-library__room {
  display: flex;
  align-items: center;
  gap: 12px;
}

.team-library__room > span {
  white-space: nowrap;
}

.team-library__meter {
  flex: 0 1 240px;
  min-width: 80px;
  height: 6px;
  overflow: hidden;
  background: var(--line);
  border-radius: var(--radius-pill);
}

.team-library__meter i {
  display: block;
  height: 100%;
  background: var(--ok);
}

.team-library__tools {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}

.team-library__search {
  flex: 1 1 240px;
  min-width: 0;
}

.team-library__kinds {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  max-width: 100%;
}

.team-library__kind {
  height: 28px;
  padding: 0 12px;
  color: var(--muted);
  cursor: pointer;
  background: none;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-pill);
  flex: none;
  transition:
    background-color var(--dur-quick) var(--ease-standard),
    color var(--dur-quick) var(--ease-standard);
}

.team-library__kind:hover {
  background: var(--fill);
}

.team-library__kind[aria-pressed='true'] {
  color: var(--surface);
  background: var(--ink);
  border-color: var(--ink);
}

.team-library__empty {
  padding: 32px 0;
  text-align: center;
}

@media (pointer: coarse) {
  .team-library__kinds {
    gap: 16px;
  }

  .team-library__kind {
    position: relative;
  }

  .team-library__kind::before {
    position: absolute;
    top: 50%;
    left: 50%;
    width: max(100%, 44px);
    height: max(100%, 44px);
    content: '';
    transform: translate(-50%, -50%);
  }

  .team-library__search :deep(.v-field) {
    min-height: 44px;
  }
}
</style>
