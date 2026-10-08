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
import type { LocationQueryRaw } from 'vue-router'
import type { MenuAction } from '@/components/common/menuAction'
import type { LibraryFile } from '../api'
import type { ListedDocument } from '../api/projectDocuments'

import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'
import { toast } from 'vuetify-sonner'

import { useRowMenu } from '@/composables/useRowMenu'

import { deleteLibraryFile, downloadFile, libraryFileRawUrl } from '../api'
import LibraryDocumentHits from '../components/library/LibraryDocumentHits.vue'
import LibraryDocumentPage from '../components/library/LibraryDocumentPage.vue'
import { useLibraryDocuments } from '../composables/useLibraryDocuments'
import { useLibraryNaming } from '../composables/useLibraryNaming'
import { type LibraryRow, useLibraryPages } from '../composables/useLibraryPages'
import { useOpenLibraryDocument } from '../composables/useOpenLibraryDocument'
import { isAgentHandle } from '../lib/authorship'
import {
  getLibraryFile,
  libraryFileBytes,
  type LibraryFolder,
  replaceLibraryFile,
  uploadLibraryFile,
} from '../lib/libraryApi'
import { leafOf } from '../lib/libraryFolders'
import { type Kind, KIND_ICONS, kindLabel, kindOf, KINDS } from '../lib/libraryKinds'
import { VIRTUAL_LIST_CONTENT_THRESHOLD } from '../lib/virtualList'

import { useCommands } from '@/commands'
import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import ConfirmDialog from '@/components/base/ConfirmDialog.vue'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import AppPage from '@/components/common/AppPage.vue'
import { useTopBarBack } from '@/components/common/topBarBack'
import VirtualList from '@/components/common/VirtualList.vue'
import { t } from '@/i18n'
import { memberName } from '@/lib/agentNames'
import { closeOverlay } from '@/lib/backOut'
import { fmtBytes } from '@/lib/changesTree'
import { relTime } from '@/lib/relTime'
import { usePageTitleStore } from '@/stores/title'
import { useWorkspaceStore } from '@/stores/workspace'
import LibraryCrumbs from '@/views/library/LibraryCrumbs.vue'
import LibraryFileDetail from '@/views/library/LibraryFileDetail.vue'
import LibraryNameDialog from '@/views/library/LibraryNameDialog.vue'
import LibraryVersionsDialog from '@/views/library/LibraryVersionsDialog.vue'

// `docId`：从 `/docs/<编号>` 进来时，整页打开的那一份文档。
const props = defineProps<{ projectId: string; docId?: string }>()

const route = useRoute()
const router = useRouter()
const { mdAndUp } = useDisplay()
const rowMenu = useRowMenu<string>()

const actionError = ref('')
const busy = ref('')
const uploading = ref(false)
// 要删的那一份或那个文件夹（文件夹连同里面的一切）。
const confirming = ref<LibraryFile | LibraryFolder | null>(null)
const replacing = ref<{ target: LibraryFile; file: File } | null>(null)
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
  listEl
)
const { rows: entries, loading, loadFailed, loadReason } = pages
watch(
  () => props.projectId,
  () => {
    confirming.value = null
    actionError.value = ''
  }
)
const inFolder = computed(() => browsing.value && !!dir.value)

function goTo(path: string) {
  const next: LocationQueryRaw = { ...route.query, dir: path || undefined }
  delete next.file
  void router.push({ query: next })
}

// 这一行写什么名字：一层一层看时写最后一层，搜出来的写完整路径。
function fileName(file: LibraryFile): string {
  return browsing.value ? leafOf(file.path) : file.path
}
// 搜索框和筛选摆不摆：最上层取完了也什么都没有、又没在找的时候不摆。取的过程中照
// 摆，换一次条件不该让搜索框闪一下。
const anything = computed(() => !(browsing.value && !dir.value && pages.exhausted.value && !entries.value.length))

function docName(doc: { title: string | null }): string {
  return doc.title || t('work.room.doc.untitled')
}
function docMeta(doc: ListedDocument): string {
  const row = workspace.members.find((m) => m.user_handle === doc.author)
  const who = isAgentHandle(doc.author) ? workspace.agentName : memberName(row) || doc.author
  return t('work.library.docMeta', { who, when: relTime(doc.updated_at) })
}

const { selectedDocId, openDocument, openRoom, openDoc, closeDoc, titled } = useOpenLibraryDocument(
  () => props.projectId,
  () => props.docId,
  { about: docs.about, renamed: pages.renameDoc },
  (message) => (actionError.value = message)
)

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
function docActions(doc: ListedDocument): MenuAction[] {
  return [
    {
      key: 'delete',
      label: t('work.library.delete'),
      icon: 'mdi-delete-outline',
      danger: true,
      onSelect: () => (confirmingDoc.value = doc),
    },
  ]
}

// ---- 看哪一份 ----------------------------------------------------------------

const selectedPath = computed(() => (typeof route.query.file === 'string' ? route.query.file : ''))
// 地址上点名的那一份：已经取回来的那几页里有就用它，没有就单独问一次。
const fetched = ref<LibraryFile | null>(null)
const selected = computed(
  () =>
    entries.value.find((row) => row.file?.path === selectedPath.value)?.file ??
    (fetched.value?.path === selectedPath.value ? fetched.value : null)
)
watch(
  [selectedPath, () => props.projectId, revision],
  async ([path, projectId]) => {
    if (!path || entries.value.some((row) => row.file?.path === path)) return
    try {
      const found = await getLibraryFile(projectId, path)
      if (selectedPath.value === path) fetched.value = found
    } catch {
      // 地址上那一份已经不在了：右边那一栏不摆，列表照常。
    }
  },
  { immediate: true }
)

function open(file: LibraryFile) {
  const next = { query: { ...route.query, file: file.path } }
  // 手机上是进了一页，返回键要能回到列表；桌面上只是换了右边那一栏。
  if (mdAndUp.value) void router.replace(next)
  else void router.push(next)
}

function close() {
  const rest = { ...route.query }
  delete rest.file
  // 手机上开一份文件是 push 进一页，所以这里退一格能真的把它弹掉；桌面上开一份是
  // replace（只换右边那一栏），退一格会退到资料库外面去 —— closeOverlay 认来路。
  closeOverlay(router, { query: rest })
}

useTopBarBack(() =>
  !mdAndUp.value && selectedDocId.value
    ? { label: t('work.library.backToList'), onBack: closeDoc }
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

// ---- 放进来、换新、拿走、扔掉 ---------------------------------------------------

const picker = ref<HTMLInputElement | null>(null)
const replacePicker = ref<HTMLInputElement | null>(null)
let replaceTarget: LibraryFile | null = null

async function upload(list: File[]) {
  if (!list.length) return
  uploading.value = true
  actionError.value = ''
  let done = 0
  try {
    for (const file of list) {
      await uploadLibraryFile(props.projectId, file, dir.value)
      done++
    }
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : t('work.library.uploadFailed')
  } finally {
    uploading.value = false
    if (done) toast(t('work.library.uploaded', { n: done }))
    pages.reset()
  }
}

function onPicked(event: Event) {
  const input = event.target as HTMLInputElement
  void upload(Array.from(input.files ?? []))
  input.value = ''
}

const dragging = ref(false)
function onDrop(event: DragEvent) {
  dragging.value = false
  void upload(Array.from(event.dataTransfer?.files ?? []))
}

function pickReplacement(file: LibraryFile) {
  replaceTarget = file
  replacePicker.value?.click()
}

function onReplacementPicked(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (file && replaceTarget) replacing.value = { target: replaceTarget, file }
}

async function replace() {
  const pending = replacing.value
  if (!pending) return
  replacing.value = null
  busy.value = pending.target.path
  actionError.value = ''
  try {
    await replaceLibraryFile(props.projectId, pending.target.path, pending.file)
    revision.value++
    toast(t('work.library.replacedDone'))
    pages.reset()
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : t('work.library.replaceFailed')
  } finally {
    busy.value = ''
  }
}

// 「版本…」开着的是哪一份（null = 关着）。恢复了一版：预览换新、清单重读。
const versionsOf = ref<string | null>(null)
function onRestored(_path: string, version: number) {
  revision.value++
  toast(t('work.library.restoredDone', { n: version }))
  pages.reset()
}

async function download(file: LibraryFile) {
  actionError.value = ''
  try {
    await downloadFile(libraryFileRawUrl(props.projectId, file.path), file.path.split('/').pop() || file.path)
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : t('work.library.downloadFailed')
  }
}

/** 确认框点「删除」：这时 `confirming` 一定是有值的，取出来交给 `remove`。 */
function confirmRemove() {
  const file = confirming.value
  if (file) void remove(file)
}

async function remove(target: LibraryFile | LibraryFolder) {
  confirming.value = null
  busy.value = target.path
  actionError.value = ''
  const gone = (path: string) => path === target.path || path.startsWith(`${target.path}/`)
  try {
    await deleteLibraryFile(props.projectId, target.path)
    pages.drop((row) => gone(row.file?.path ?? row.folder?.path ?? ''))
    if (gone(selectedPath.value)) close()
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : t('work.library.deleteFailed')
  } finally {
    busy.value = ''
  }
}

// ---- 文件夹：挪动、改名、新建 ---------------------------------------------------

const naming = useLibraryNaming({
  projectId: () => props.projectId,
  dir: () => dir.value,
  goTo,
  opened: () => selectedPath.value,
  reopen: (path) => void router.replace({ query: { ...route.query, file: path } }),
  reload: async () => pages.reset(),
})

function folderActions(folder: LibraryFolder): MenuAction[] {
  return [
    {
      key: 'move',
      label: t('work.library.move'),
      icon: 'mdi-folder-move-outline',
      onSelect: () => naming.start({ path: folder.path }),
    },
    {
      key: 'delete',
      label: t('work.library.delete'),
      icon: 'mdi-delete-outline',
      danger: true,
      loading: busy.value === folder.path,
      onSelect: () => (confirming.value = folder),
    },
  ]
}

function fileActions(file: LibraryFile): MenuAction[] {
  return [
    {
      key: 'download',
      label: t('work.library.download'),
      icon: 'mdi-download-outline',
      onSelect: () => void download(file),
    },
    {
      key: 'replace',
      label: t('work.library.replace'),
      icon: 'mdi-file-replace-outline',
      onSelect: () => pickReplacement(file),
    },
    {
      key: 'move',
      label: t('work.library.move'),
      icon: 'mdi-folder-move-outline',
      onSelect: () => naming.start({ path: file.path }),
    },
    {
      key: 'versions',
      label: t('work.library.versions'),
      icon: 'mdi-history',
      onSelect: () => (versionsOf.value = file.path),
    },
    {
      key: 'delete',
      label: t('work.library.delete'),
      icon: 'mdi-delete-outline',
      danger: true,
      loading: busy.value === file.path,
      onSelect: () => (confirming.value = file),
    },
  ]
}

useCommands(() => {
  const open = selected.value
  // 手机上看着一份文件时，顶栏的 ⋯ 是这一份的操作；否则页头上是「上传文件」。
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

// ---- 摆出来 ------------------------------------------------------------------

function rowMeta(file: LibraryFile): string {
  return [file.added_by ?? t('work.library.unknownSource'), relTime(file.added_at), fmtBytes(file.bytes)].join(' · ')
}

function folderMeta(folder: LibraryFolder): string {
  return t('work.library.folderMeta', {
    n: folder.count,
    when: relTime(new Date(folder.modified * 1000).toISOString()),
  })
}

// 行的身份（和 v-for 的 key 同义）：路径。同名不覆盖，所以路径唯一。写成具名函数而
// 不是模板里的箭头：模板里那个箭头参数没有类型来源，strict 下会报隐式 any。
function entryKey(row: unknown): string {
  return (row as LibraryRow).key
}

function read(file: LibraryFile) {
  return (asPdf: boolean) => libraryFileBytes(props.projectId, file.path, asPdf)
}
</script>

<template>
  <LibraryDocumentPage
    v-if="selectedDocId"
    :project-id="projectId"
    :document="openDocument"
    :error="actionError"
    :agent-name="workspace.agentName"
    :agent-handle="workspace.agentHandle"
    :members="workspace.members"
    :topic-list="workspace.topics"
    @titled="titled"
    @delete="confirmingDoc = openDocument"
    @open-topic="openRoom"
  />
  <AppPage v-else :title="selected && !mdAndUp ? selected.path : t('navigation.project.library')" width="full">
    <div
      class="library"
      :class="{ 'library--dragging': dragging }"
      @dragover.prevent="dragging = true"
      @dragleave.self="dragging = false"
      @drop.prevent="onDrop"
    >
      <input ref="picker" type="file" multiple hidden @change="onPicked" />
      <input ref="replacePicker" type="file" hidden @change="onReplacementPicked" />

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

        <ul v-if="entries.length" class="library__rows">
          <!-- Long libraries go through VirtualList: past VIRTUAL_LIST_CONTENT_THRESHOLD
               (lib/virtualList.ts) only the rows in view stay mounted, below it this is
               the plain list it always was. `item-as="li"` keeps the ul > li structure
               in both paths — virtua only wraps each row; the row itself carries the
               flex layout, the buttons and the actions menu, so keyboard focus and the
               ⋯ menu work exactly as before. virtua owns the li, so the right-click
               handler sits on the row div it wraps. -->
          <VirtualList
            :items="entries"
            :item-key="entryKey"
            :scroll-parent="listEl"
            :threshold="VIRTUAL_LIST_CONTENT_THRESHOLD"
            :estimated-size="58"
            item-as="li"
            item-role="listitem"
          >
            <template #item="{ item }">
              <div v-if="item.folder" class="library-row" @contextmenu="rowMenu.open(item.key, $event)">
                <button type="button" class="library-row__open" @click="goTo(item.folder.path)">
                  <v-icon icon="mdi-folder-outline" size="20" class="library-row__icon" />
                  <span class="library-row__id">
                    <span class="library-row__name t-body">{{ item.folder.name }}</span>
                    <span class="t-meta c-faint">{{ folderMeta(item.folder) }}</span>
                  </span>
                </button>
                <AdaptiveMenu
                  v-bind="rowMenu.bind(item.key)"
                  :actions="folderActions(item.folder)"
                  :title="item.folder.name"
                >
                  <template #activator="{ props: menuProps }">
                    <BaseButton
                      v-bind="menuProps"
                      icon="mdi-dots-horizontal"
                      size="sm"
                      class="tap-target"
                      :loading="busy === item.folder.path"
                      :aria-label="t('work.library.actionsOf', { name: item.folder.name })"
                    />
                  </template>
                </AdaptiveMenu>
              </div>
              <div v-else-if="item.doc" class="library-row" @contextmenu="rowMenu.open(item.key, $event)">
                <button type="button" class="library-row__open" @click="openDoc(item.doc.id)">
                  <v-icon icon="mdi-file-document-edit-outline" size="20" class="library-row__icon" />
                  <span class="library-row__id">
                    <span class="library-row__name t-body">{{ docName(item.doc) }}</span>
                    <span class="t-meta c-faint">{{ docMeta(item.doc) }}</span>
                  </span>
                </button>
                <AdaptiveMenu
                  v-bind="rowMenu.bind(item.key)"
                  :actions="docActions(item.doc)"
                  :title="docName(item.doc)"
                >
                  <template #activator="{ props: menuProps }">
                    <BaseButton
                      v-bind="menuProps"
                      icon="mdi-dots-horizontal"
                      size="sm"
                      class="tap-target"
                      :aria-label="t('work.library.actionsOf', { name: docName(item.doc) })"
                    />
                  </template>
                </AdaptiveMenu>
              </div>
              <div
                v-else-if="item.file"
                class="library-row"
                :class="{ 'library-row--on': item.file.path === selectedPath }"
                @contextmenu="rowMenu.open(item.key, $event)"
              >
                <button type="button" class="library-row__open" @click="open(item.file)">
                  <v-icon :icon="KIND_ICONS[kindOf(item.file.path)]" size="20" class="library-row__icon" />
                  <span class="library-row__id">
                    <span class="library-row__name t-body">{{ fileName(item.file) }}</span>
                    <span class="t-meta c-faint">{{ rowMeta(item.file) }}</span>
                  </span>
                </button>
                <AdaptiveMenu
                  v-bind="rowMenu.bind(item.key)"
                  :actions="fileActions(item.file)"
                  :title="fileName(item.file)"
                >
                  <template #activator="{ props: menuProps }">
                    <BaseButton
                      v-bind="menuProps"
                      icon="mdi-dots-horizontal"
                      size="sm"
                      class="tap-target"
                      :loading="busy === item.file.path"
                      :aria-label="t('work.library.actionsOf', { name: fileName(item.file) })"
                    />
                  </template>
                </AdaptiveMenu>
              </div>
            </template>
          </VirtualList>
        </ul>

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
        :project-id="projectId"
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
      :project-id="projectId"
      :path="versionsOf"
      :can-restore="true"
      :fmt-bytes="fmtBytes"
      @close="versionsOf = null"
      @restored="onRestored"
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
   有滚动条告诉你还有，看上去像少了两个筛选。折成两行多占一行列表的高度，但六个
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

.library__rows {
  display: flex;
  padding: 0;
  margin: 0;
  list-style: none;
  flex-direction: column;
  gap: 2px;
}

.library-row {
  display: flex;
  align-items: center;
  gap: 4px;
  padding-right: 4px;
  border-radius: var(--radius-md);
  transition: background-color var(--dur-quick) var(--ease-standard);
}

.library-row:hover {
  background: var(--fill);
}

.library-row--on {
  background: var(--line-2);
}

.library-row__open {
  display: flex;
  min-width: 0;
  min-height: 56px;
  padding: 8px 12px;
  color: inherit;
  text-align: left;
  cursor: pointer;
  background: none;
  border: 0;
  flex: 1 1 auto;
  align-items: center;
  gap: 12px;
}

.library-row__icon {
  flex: none;
  color: var(--muted);
}

.library-row__id {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.library-row__name {
  color: var(--ink);
  overflow-wrap: anywhere;
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
