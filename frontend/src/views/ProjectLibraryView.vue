<script setup lang="ts">
// 资料库：你给这个项目的文件。
//
// 这一页回答四件事：给进来的都有什么（按名字搜、按类型筛），每一份是谁在哪次对话里
// 给的，里面写了什么（点开就看，不用先下载），以及怎么放进来、换新、扔掉。放进来
// 有两条路：对话里上传（一份资料多半是在说某件事的时候给的），和这一页上直接放
// ——整理资料的人不必为了上传先找一个房间。
//
// 同名不覆盖，所以列表里会出现 `预算表(2).xlsx`：两次上传就是两份。人明确说「这是
// 同一份的新版本」才替换，旧的那一份留着。
//
// 名字里的 `/` 是文件夹。不搜也不筛时一层一层地看（在哪一层记在 `?dir=` 上）；一搜
// 或一筛就是整个资料库里对得上的那些，带着完整的路径。
//
// 看哪一份记在地址上（`?file=`）：刷新、把链接发给别人都回到这一份。手机上它是整一
// 页，← 回到列表。
//
// 页面本身只做三件事：把条目取回来（`useLibraryPages`／`useLibraryDocuments`）、把看
// 哪一份和写操作接到地址与状态上（`useLibrarySelection`／`useLibraryTransfers`）、摆出
// 这一列和右栏。每一行长什么样在 `library/LibraryRows.vue`。
import type { LocationQueryRaw } from 'vue-router'
import type { ListedDocument } from '../api/projectDocuments'
import type { LibraryFile, LibraryFolder } from '../lib/libraryApi'

import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'
import { useQuery } from '@tanstack/vue-query'

import DocMoveDialog from '../components/library/DocMoveDialog.vue'
import DocShareDialog from '../components/library/DocShareDialog.vue'
import LibraryDocumentHits from '../components/library/LibraryDocumentHits.vue'
import LibraryDocumentPage from '../components/library/LibraryDocumentPage.vue'
import { useDocumentMove } from '../composables/useDocumentMove'
import { useDocumentShare } from '../composables/useDocumentShare'
import { useLibraryDocuments } from '../composables/useLibraryDocuments'
import { useLibraryNaming } from '../composables/useLibraryNaming'
import { useLibraryPages } from '../composables/useLibraryPages'
import { useLibrarySelection } from '../composables/useLibrarySelection'
import { useLibraryTransfers } from '../composables/useLibraryTransfers'
import { useLibraryVersions } from '../composables/useLibraryVersions'
import { useOpenLibraryDocument } from '../composables/useOpenLibraryDocument'
import { libraryFileBytes } from '../lib/libraryApi'
import { leafOf } from '../lib/libraryFolders'
import { type Kind, kindLabel, KINDS } from '../lib/libraryKinds'

import { useCommands } from '@/commands'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import ConfirmDialog from '@/components/base/ConfirmDialog.vue'
import AppPage from '@/components/common/AppPage.vue'
import { useTopBarBack } from '@/components/common/topBarBack'
import { t } from '@/i18n'
import { fmtBytes } from '@/lib/changesTree'
import { refRoute } from '@/lib/refDirectory'
import { TASK_ROUTE_NAMES } from '@/lib/spaceRouteNames'
import { TASK_FOLDER } from '@/lib/taskRevision'
import { queryClient } from '@/query/client'
import { projectQuery } from '@/query/project'
import { usePageTitleStore } from '@/stores/title'
import { useWorkspaceStore } from '@/stores/workspace'
import LibraryCrumbs from '@/views/library/LibraryCrumbs.vue'
import LibraryFileDetail from '@/views/library/LibraryFileDetail.vue'
import LibraryNameDialog from '@/views/library/LibraryNameDialog.vue'
import LibraryRows from '@/views/library/LibraryRows.vue'
import LibraryVersionsDialog from '@/views/library/LibraryVersionsDialog.vue'

// `docId`：从 `/docs/<编号>` 进来时，整页打开的那一份文档。
const props = defineProps<{ projectId: string; docId?: string }>()

const route = useRoute()
const router = useRouter()
const { mdAndUp } = useDisplay()

const actionError = ref('')
// 同一个名字被替换以后，预览要重新读：换一次，这个数加一。
const revision = ref(0)

// ---- 找：按名字、按类型 -------------------------------------------------------

const query = ref('')
const kind = ref<Kind>('all')
// 「筛选空」那句下面的一键清除：清掉搜索框和类型筛选，列表自己回来（§8.1）。
function clearFilters() {
  query.value = ''
  kind.value = 'all'
}
// ---- 文档：和文件摆进同一张列表，最近改过的在前 ------------------------------------

const docs = useLibraryDocuments(
  () => props.projectId,
  () => query.value
)
const workspace = useWorkspaceStore()
const searching = computed(() => !!query.value.trim())
// 一层一层地看：不搜也不筛的时候。
const browsing = computed(() => !searching.value && kind.value === 'all')
const dir = computed(() => (typeof route.query.dir === 'string' ? route.query.dir : ''))
// 文件那一列自己的滚动容器（`.library__list` 上写着 overflow-y: auto）：滚到离底不远
// 就取下一页；行数过门槛时也交给 VirtualList——见 lib/virtualList.ts 的门槛那段。
const listEl = ref<HTMLElement | null>(null)
const pages = useLibraryPages(
  () => props.projectId,
  () => ({ dir: browsing.value ? dir.value : '', q: query.value, kind: kind.value }),
  listEl,
  () => !props.docId
)
const { rows: entries, loading, loadFailed, loadReason } = pages
const inFolder = computed(() => browsing.value && !!dir.value)
// 这一层是只读的（项目资料库里的 `题目/`）：服务端在每一行上说，这里只看它。
const readonlyHere = computed(
  () => inFolder.value && entries.value.some((row) => row.file?.readonly || row.folder?.readonly)
)

// 在 `题目/` 这一层：上面写一行它来自哪道题、现在第几版，连到题目页（`GET /projects/{id}`
// 的 `task`，看的人看得到这个文件夹时才有）。
const project = useQuery(
  computed(() => ({
    ...projectQuery(props.projectId),
    enabled: !props.docId && browsing.value && dir.value === TASK_FOLDER,
  })),
  queryClient
)
const taskHere = computed(() => (browsing.value && dir.value === TASK_FOLDER ? project.data.value?.task ?? null : null))

// 拖进 `题目/` 的文件放不进去：说一句为什么，而不是什么也不发生。
function dropHere(event: DragEvent) {
  if (!readonlyHere.value) return onDrop(event)
  dragging.value = false
  actionError.value = t('work.library.readonlyDrop', { folder: dir.value })
}

function goTo(path: string) {
  const next: LocationQueryRaw = { ...route.query, dir: path || undefined }
  delete next.file
  void router.push({ query: next })
}

// 搜索框和筛选摆不摆：最上层取完了也什么都没有、又没在找的时候不摆。取的过程中照
// 摆，换一次条件不该让搜索框闪一下。
const anything = computed(() => !(browsing.value && !dir.value && pages.exhausted.value && !entries.value.length))

function docName(doc: { title: string | null }): string {
  return doc.title || t('work.room.doc.untitled')
}

const { selectedDocId, openDocument, documentReview, fromThread, openRoom, openDoc, closeDoc, titled } =
  useOpenLibraryDocument(
    () => props.projectId,
    () => props.docId,
    { about: docs.about, renamed: pages.renameDoc },
    (message) => (actionError.value = message)
  )

const { selectedPath, selected, open, close } = useLibrarySelection({
  shelf: () => props.projectId,
  entries,
  revision,
  mdAndUp,
  router,
  route,
})

const transfers = useLibraryTransfers({
  shelf: () => props.projectId,
  dir: () => dir.value,
  reset: pages.reset,
  drop: pages.drop,
  opened: () => selectedPath.value,
  close,
  changed: () => (revision.value += 1),
  error: actionError,
})
const {
  busy,
  uploading,
  progress: uploadProgress,
  confirming,
  replacing,
  dragging,
  versionsOf,
  picker,
  replacePicker,
  onPicked,
  onDrop,
  onReplacementPicked,
  download,
  pickReplacement,
  replace,
  onRestored,
  confirmRemove,
} = transfers

const history = useLibraryVersions(
  () => props.projectId,
  () => versionsOf.value,
  onRestored
)

useTopBarBack(() =>
  !mdAndUp.value && selectedDocId.value
    ? { label: t(fromThread.value ? 'work.room.thread.backToThread' : 'work.library.backToList'), onBack: closeDoc }
    : !mdAndUp.value && selectedPath.value
      ? { label: t('work.library.backToList'), onBack: close }
      : null
)

// 手机顶栏写的是正看着的那一份的名字。
const titles = usePageTitleStore()
watch(
  () =>
    openDocument.value
      ? docName(openDocument.value)
      : selected.value && !mdAndUp.value
        ? selected.value.path
        : t('navigation.project.library'),
  (title) => titles.setDynamicTitle(title, 'project-library'),
  { immediate: true }
)

// ---- 文件夹：挪动、改名、新建 ---------------------------------------------------

const naming = useLibraryNaming({
  shelf: () => props.projectId,
  dir: () => dir.value,
  goTo,
  opened: () => selectedPath.value,
  reopen: (path) => void router.replace({ query: { ...route.query, file: path } }),
  reload: async () => pages.reset(),
})

// ---- 行上那几件事落到这一页 -----------------------------------------------------

/** 改名走名字框；版本和删除都只是先摆出一个对话框，答应之后才动。 */
function startRename(path: string) {
  naming.start({ path })
}
function showVersions(file: LibraryFile) {
  versionsOf.value = file.path
}
function askRemove(target: LibraryFile | LibraryFolder) {
  confirming.value = target
}

// ---- 文档：新建、另存、删掉 -----------------------------------------------------

// 新建一份空的（`copyOf` 不给），或者另存对话里那一份；建好就打开。
async function make(copyOf?: string) {
  actionError.value = ''
  try {
    openDoc(await docs.make(copyOf))
    pages.reset()
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : t('work.library.newDocFailed')
  }
}

const confirmingDoc = ref<{ id: string; title: string | null } | null>(null)
function askRemoveDoc(doc: ListedDocument) {
  confirmingDoc.value = doc
}
async function removeDoc() {
  const doc = confirmingDoc.value
  confirmingDoc.value = null
  if (!doc) return
  actionError.value = ''
  try {
    await docs.remove(doc.id)
    pages.drop((row) => row.doc?.id === doc.id)
    if (selectedDocId.value === doc.id)
      void router.replace({ name: 'project-library', params: { projectId: props.projectId } })
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : t('work.library.deleteFailed')
  }
}

// 挪出这个项目的文档从列表里拿掉，页面跟着去它的新地址。
const mover = useDocumentMove((to) => {
  const id = mover.state.documentId
  pages.drop((row) => row.doc?.id === id)
  void router.push(to)
})

const sharer = useDocumentShare()

// ---- 顶栏上的操作 --------------------------------------------------------------

useCommands(() => {
  const open = selected.value
  // 手机上看着一份文件时，顶栏的 ⋯ 是这一份的操作；否则页头上是「上传文件」。
  if (!mdAndUp.value && open?.readonly) {
    return [
      {
        id: 'library.versions',
        title: t('work.library.versions'),
        icon: 'mdi-history',
        palette: false as const,
        header: {},
        run: () => (versionsOf.value = open.path),
      },
    ]
  }
  if (!mdAndUp.value && open) {
    return [
      {
        id: 'library.replace',
        title: t('work.library.replace'),
        icon: 'mdi-file-replace-outline',
        palette: false as const,
        header: {},
        run: () => pickReplacement(open),
      },
      {
        id: 'library.versions',
        title: t('work.library.versions'),
        icon: 'mdi-history',
        palette: false as const,
        header: {},
        run: () => (versionsOf.value = open.path),
      },
      {
        id: 'library.delete',
        title: t('work.library.delete'),
        icon: 'mdi-delete-outline',
        danger: true,
        palette: false as const,
        header: {},
        run: () => (confirming.value = open),
      },
    ]
  }
  // 一份文档开着时，它的操作在文档自己的顶栏上。
  if (selectedDocId.value) return []
  if (readonlyHere.value) return []
  return [
    {
      id: 'library.newFolder',
      title: t('work.library.newFolder'),
      icon: 'mdi-folder-plus-outline',
      palette: false as const,
      header: {},
      run: () => naming.start({ newFolder: true }),
    },
    {
      id: 'library.upload',
      title: t('work.library.upload'),
      icon: 'mdi-upload',
      palette: false as const,
      loading: uploading.value,
      header: {},
      run: () => picker.value?.click(),
    },
    {
      id: 'library.newDoc',
      title: t('work.library.newDoc'),
      icon: 'mdi-plus',
      palette: false as const,
      loading: docs.making.value === '',
      header: { primary: true, accent: true },
      run: () => void make(),
    },
  ]
})

function read(file: LibraryFile) {
  return (asPdf: boolean) => libraryFileBytes(props.projectId, file.path, asPdf)
}
</script>

<template>
  <LibraryDocumentPage
    v-if="selectedDocId"
    :project-id="projectId"
    :document="openDocument"
    :review="documentReview"
    :error="actionError"
    :agent-name="workspace.agentName"
    :agent-handle="workspace.agentHandle"
    :members="workspace.members"
    :topic-list="workspace.topics"
    @titled="titled"
    @delete="confirmingDoc = openDocument"
    @move="mover.open(selectedDocId)"
    @share="sharer.open(selectedDocId)"
    @open-topic="(id: string, block?: string) => router.push(refRoute(projectId, id, block))"
  />
  <AppPage v-else :title="selected && !mdAndUp ? selected.path : t('navigation.project.library')" width="full">
    <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- a drop target for files dragged in; the upload action is the keyboard's way -->
    <div
      class="library"
      :class="{ 'library--dragging': dragging }"
      @dragover.prevent="dragging = true"
      @dragleave.self="dragging = false"
      @drop.prevent="dropHere"
    >
      <input ref="picker" type="file" multiple hidden :aria-label="t('work.library.upload')" @change="onPicked" />
      <input
        ref="replacePicker"
        type="file"
        hidden
        :aria-label="t('work.library.replace')"
        @change="onReplacementPicked"
      />

      <!-- 列表：手机上看着一份文件时让出整页。 -->
      <section v-if="mdAndUp || !selected" ref="listEl" class="library__list">
        <p class="t-body c-muted library__intro" :title="t('work.library.dropHint')">{{ t('work.library.intro') }}</p>
        <!-- The list failed to load: replace it in place with an error and a
             retry, not a red empty list. -->
        <BaseLoadError
          v-if="loadFailed"
          class="library__load-error"
          :title="t('work.library.loadError')"
          :error="loadReason || null"
          @retry="pages.reset()"
        />
        <p v-if="actionError" role="alert" class="t-body c-danger">{{ actionError }}</p>
        <p v-if="uploadProgress" role="status" class="t-body c-muted">
          {{
            uploadProgress.percent >= 100
              ? t('work.library.uploadProcessing', uploadProgress)
              : t('work.library.uploadProgress', uploadProgress)
          }}
        </p>

        <div v-if="anything" class="library__tools">
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
            class="library__search"
          />
          <div class="library__kinds" role="group" :aria-label="t('work.library.kindsLabel')">
            <button
              v-for="k in KINDS"
              :key="k"
              type="button"
              class="library__kind t-meta"
              :aria-pressed="kind === k"
              @click="kind = k"
            >
              {{ kindLabel(k) }}
            </button>
          </div>
        </div>

        <LibraryCrumbs v-if="inFolder" :dir="dir" :root="t('navigation.project.library')" @go="goTo" />
        <!-- Inside the read-only task folder: which task it belongs to, its version now, and its page. -->
        <p v-if="taskHere" class="t-body c-muted library__task" data-testid="library-task-folder">
          <span data-user-content>{{
            taskHere.version
              ? t('work.library.taskFolderFrom', { name: taskHere.name, version: taskHere.version })
              : t('work.library.taskFolderFromUnversioned', { name: taskHere.name })
          }}</span>
          <router-link
            class="library__task-link"
            :to="{
              name: TASK_ROUTE_NAMES.detail,
              params: { spaceId: String(taskHere.space_id), taskId: String(taskHere.id) },
            }"
            >{{ t('work.library.taskFolderOpen') }}</router-link
          >
        </p>

        <div
          v-if="loading && !entries.length"
          class="py-8 text-center"
          role="status"
          :aria-label="t('work.library.loading')"
        >
          <v-progress-circular indeterminate size="28" color="primary" />
        </div>

        <LibraryDocumentHits
          v-if="searching && docs.found.value"
          :library="docs.found.value.library"
          :rooms="docs.found.value.rooms"
          :keeping="docs.making.value"
          @open="openDoc"
          @open-room="openRoom"
          @keep="make"
        />

        <LibraryRows
          v-if="entries.length"
          :rows="entries"
          :scroller="listEl"
          :selected-path="selectedPath"
          :busy="busy"
          :browsing="browsing"
          :members="workspace.members"
          :agent-name="workspace.agentName"
          @open-file="open"
          @open-folder="goTo"
          @open-doc="openDoc"
          @download="download"
          @replace="pickReplacement"
          @rename="startRename"
          @versions="showVersions"
          @delete="askRemove"
          @delete-doc="askRemoveDoc"
        />

        <BaseEmptyState
          v-else-if="inFolder && !loading"
          size="inline"
          align="center"
          class="library__empty"
          :title="t('work.library.folderEmpty')"
        />
        <!-- Files exist but the search/type filter hid them: say "filtered out",
             distinct from "no files yet", and offer one-click clear (§8.1). -->
        <BaseEmptyState
          v-else-if="
            !browsing &&
            !loading &&
            !(searching && docs.searching.value) &&
            !(searching && (docs.found.value?.library.length || docs.found.value?.rooms.length))
          "
          size="inline"
          align="center"
          class="library__empty"
          :title="t('work.library.noMatch')"
          :action="t('work.library.clearFilters')"
          @action="clearFilters"
        />
        <BaseEmptyState
          v-else-if="!loadFailed && !loading"
          size="inline"
          align="center"
          class="library__empty"
          :title="t('work.library.empty')"
        />
        <div
          v-if="loading && entries.length"
          class="py-4 text-center"
          role="status"
          :aria-label="t('work.library.loading')"
        >
          <v-progress-circular indeterminate size="20" color="primary" />
        </div>
      </section>

      <!-- 这一份：桌面上在右边一栏，手机上是整一页。 -->
      <LibraryFileDetail
        v-if="selected"
        :shelf="projectId"
        :file="selected"
        :wide="mdAndUp"
        :revision="revision"
        :busy="busy === selected.path"
        :read="read(selected)"
        @download="download(selected)"
        @replace="pickReplacement(selected)"
        @close="close"
      />
    </div>

    <LibraryVersionsDialog
      :path="versionsOf"
      :versions="history.versions.value"
      :loading="history.loading.value"
      :failed="history.failed.value"
      :action-error="history.actionError.value"
      :restoring="history.restoring.value"
      :can-restore="true"
      :fmt-bytes="fmtBytes"
      @close="versionsOf = null"
      @retry="history.load"
      @download="history.download"
      @restore="history.restore"
    />

    <!-- Replace changes what every message referencing this file reads, so say it before acting. -->
    <ConfirmDialog
      :model-value="!!replacing"
      :title="t('work.library.replaceTitle', { name: replacing?.target.path ?? '' })"
      :confirm-label="t('work.library.replaceConfirm')"
      @update:model-value="replacing = null"
      @confirm="replace"
    >
      {{ t('work.library.replaceBody', { file: replacing?.file.name ?? '' }) }}
    </ConfirmDialog>

    <!-- Deleting is irreversible, and this file may already be referenced by several
         messages: those references will stop opening, so ask before it happens. -->
    <ConfirmDialog
      :model-value="!!confirming"
      :title="t('work.library.deleteTitle', { name: confirming ? leafOf(confirming.path) : '' })"
      :confirm-label="t('work.library.delete')"
      danger
      @update:model-value="confirming = null"
      @confirm="confirmRemove"
    >
      {{
        confirming && 'count' in confirming
          ? t('work.library.deleteFolderBody', { n: confirming.count })
          : t('work.library.deleteBody')
      }}
    </ConfirmDialog>

    <LibraryNameDialog
      :model-value="!!naming.pending.value"
      v-bind="naming.dialog.value"
      :loading="naming.moving.value"
      :error="naming.error.value"
      @update:model-value="naming.pending.value = null"
      @submit="naming.submit"
    />
  </AppPage>
  <ConfirmDialog
    :model-value="!!confirmingDoc"
    :title="t('work.library.deleteTitle', { name: confirmingDoc ? docName(confirmingDoc) : '' })"
    :confirm-label="t('work.library.delete')"
    danger
    @update:model-value="confirmingDoc = null"
    @confirm="removeDoc"
  >
    {{ t('work.library.deleteDocBody') }}
  </ConfirmDialog>
  <DocMoveDialog
    v-model="mover.state.open"
    :title="mover.state.title"
    :places="mover.places.value"
    :current="mover.state.current"
    :saving="mover.state.saving"
    :error="mover.state.error"
    @move="mover.move"
  />
  <DocShareDialog
    v-model="sharer.state.open"
    :title="sharer.state.title"
    :loading="sharer.state.loading"
    :may-share="sharer.state.mayShare"
    :url="sharer.url.value"
    :busy="sharer.state.busy"
    :error="sharer.state.error"
    @share="sharer.share"
    @unshare="sharer.unshare"
    @copy="sharer.copy"
  />
</template>

<style scoped>
.library {
  display: flex;
  height: 100%;
  min-height: 0;
  outline: 2px dashed transparent;
  outline-offset: -8px;
  transition: outline-color var(--dur-quick) var(--ease-standard);
}

.library--dragging {
  outline-color: var(--accent);
}

.library__task {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  margin: 0;
}

.library__task-link {
  color: var(--accent-ink);
  text-decoration: none;
}

.library__task-link:hover {
  text-decoration: underline;
}

.library__list {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
  padding: 20px 24px 32px;
  overflow-y: auto;
}

.library__intro {
  margin: 0;
}

.library__load-error {
  padding: 8px 0;
}

.library__tools {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}

.library__search {
  flex: 1 1 240px;
  min-width: 0;
}

/* 一行排不下就换行，不横着滑：手机上六个筛选横滑时右边那几个被切在屏幕外，又没
   有滚动条告诉你还有，看上去像少了两个。折成两行多占一行列表的高度，但六个
   都在。 */
.library__kinds {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  max-width: 100%;
}

.library__kind {
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

.library__kind:hover {
  background: var(--fill);
}

.library__kind[aria-pressed='true'] {
  color: var(--surface);
  background: var(--ink);
  border-color: var(--ink);
}

.library__empty {
  padding: 32px 0;
  text-align: center;
}

/* 手指点得中（设计系统 §10.1）。筛选是一排 28px 的小药丸、搜索框只有 40px 高，
   触屏上都够不到 44。撑开能点的范围、把搜索框抬到 44 高；药丸之间因此先拉开，
   撑开的部分互不盖住，一次点中一个筛选。 */
@media (pointer: coarse) {
  .library__kinds {
    gap: 16px;
  }

  .library__kind {
    position: relative;
  }

  .library__kind::before {
    position: absolute;
    top: 50%;
    left: 50%;
    width: max(100%, 44px);
    height: max(100%, 44px);
    content: '';
    transform: translate(-50%, -50%);
  }

  .library__search :deep(.v-field) {
    min-height: 44px;
  }
}
</style>
