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
// 看哪一份记在地址上（`?file=`）：刷新、把链接发给别人都回到这一份。手机上它是整一
// 页，← 回到列表。
import type { MenuAction } from '@/components/common/menuAction'
import type { LibraryFile } from '../api'

import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'
import { toast } from 'vuetify-sonner'

import { useRowMenu } from '@/composables/useRowMenu'

import { ApiError, deleteLibraryFile, downloadFile, libraryFileRawUrl, listProjectLibrary } from '../api'
import { libraryFileBytes, replaceLibraryFile, uploadLibraryFile } from '../lib/libraryApi'
import { VIRTUAL_LIST_CONTENT_THRESHOLD } from '../lib/virtualList'

import { useCommands } from '@/commands'
import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import ConfirmDialog from '@/components/base/ConfirmDialog.vue'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import AppPage from '@/components/common/AppPage.vue'
import FileBytesPreview from '@/components/common/FileBytesPreview.vue'
import { useTopBarBack } from '@/components/common/topBarBack'
import VirtualList from '@/components/common/VirtualList.vue'
import { t } from '@/i18n'
import { closeOverlay } from '@/lib/backOut'
import { relTime } from '@/lib/relTime'
import { topicTitle } from '@/lib/topicState'
import { usePageTitleStore } from '@/stores/title'
import LibraryVersionsDialog from '@/views/library/LibraryVersionsDialog.vue'

const props = defineProps<{ projectId: string }>()

const route = useRoute()
const router = useRouter()
const { mdAndUp } = useDisplay()
const rowMenu = useRowMenu<string>()

const files = ref<LibraryFile[]>([])
const loading = ref(false)
// 读整份清单失败：整块换成失败块（§3.10），失败块自己拿服务端那句原话和重试。
const loadError = ref(false)
const loadErrorDetail = ref<string | null>(null)
// 401/403 是「不给你看」，不是「这次没读到」：失败块换成无权限形态、不给重试。
const loadForbidden = ref(false)
const actionError = ref('')
const busy = ref('')
const uploading = ref(false)
const confirming = ref<LibraryFile | null>(null)
const replacing = ref<{ target: LibraryFile; file: File } | null>(null)
// 同一个名字被替换以后，预览要重新读：换一次，这个数加一。
const revision = ref(0)
// 文件那一列自己的滚动容器（`.library__list` 上写着 overflow-y: auto）。行数过门槛
// 时交给 VirtualList，得把这份容器递给它——见 lib/virtualList.ts 的门槛那段。
const listEl = ref<HTMLElement | null>(null)

// ---- 读 --------------------------------------------------------------------

async function load() {
  const projectId = props.projectId
  loading.value = true
  loadError.value = false
  loadErrorDetail.value = null
  loadForbidden.value = false
  try {
    const listed = await listProjectLibrary(projectId)
    if (props.projectId !== projectId) return
    files.value = listed.data
  } catch (e) {
    if (props.projectId !== projectId) return
    loadError.value = true
    loadErrorDetail.value = e instanceof Error ? e.message : null
    loadForbidden.value = e instanceof ApiError && (e.status === 401 || e.status === 403)
  } finally {
    if (props.projectId === projectId) loading.value = false
  }
}

watch(
  () => props.projectId,
  () => {
    files.value = []
    confirming.value = null
    actionError.value = ''
    void load()
  },
  { immediate: true }
)

// ---- 找：按名字、按类型 -------------------------------------------------------

type Kind = 'all' | 'doc' | 'sheet' | 'pdf' | 'image' | 'other'
const KIND_SUFFIXES: Record<Exclude<Kind, 'all' | 'other'>, string[]> = {
  doc: ['doc', 'docx', 'odt', 'rtf', 'md', 'markdown', 'txt', 'ppt', 'pptx', 'odp', 'pages', 'key'],
  sheet: ['xls', 'xlsx', 'csv', 'ods', 'numbers'],
  pdf: ['pdf'],
  image: ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'heic'],
}
const KINDS: Kind[] = ['all', 'doc', 'sheet', 'pdf', 'image', 'other']
const KIND_ICONS: Record<Exclude<Kind, 'all'>, string> = {
  doc: 'mdi-file-document-outline',
  sheet: 'mdi-file-table-outline',
  pdf: 'mdi-file-pdf-box',
  image: 'mdi-file-image-outline',
  other: 'mdi-file-outline',
}

function kindOf(path: string): Exclude<Kind, 'all'> {
  const suffix = path.includes('.') ? path.split('.').pop()!.toLowerCase() : ''
  for (const [kind, suffixes] of Object.entries(KIND_SUFFIXES)) {
    if (suffixes.includes(suffix)) return kind as Exclude<Kind, 'all'>
  }
  return 'other'
}

function kindLabel(kind: Kind): string {
  return {
    all: t('work.library.kinds.all'),
    doc: t('work.library.kinds.doc'),
    sheet: t('work.library.kinds.sheet'),
    pdf: t('work.library.kinds.pdf'),
    image: t('work.library.kinds.image'),
    other: t('work.library.kinds.other'),
  }[kind]
}

const query = ref('')
const kind = ref<Kind>('all')
const shown = computed(() => {
  const q = query.value.trim().toLowerCase()
  return files.value.filter(
    (file) => (kind.value === 'all' || kindOf(file.path) === kind.value) && (!q || file.path.toLowerCase().includes(q))
  )
})

// ---- 看哪一份 ----------------------------------------------------------------

const selectedPath = computed(() => (typeof route.query.file === 'string' ? route.query.file : ''))
const selected = computed(() => files.value.find((file) => file.path === selectedPath.value) ?? null)

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
  !mdAndUp.value && selectedPath.value ? { label: t('work.library.backToList'), onBack: close } : null
)

// 手机顶栏写的是正看着的那一份的名字。
const titles = usePageTitleStore()
watch(
  () => (selected.value && !mdAndUp.value ? selected.value.path : t('navigation.project.library')),
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
      await uploadLibraryFile(props.projectId, file)
      done++
    }
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : t('work.library.uploadFailed')
  } finally {
    uploading.value = false
    if (done) toast(t('work.library.uploaded', { n: done }))
    await load()
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
    await load()
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
  void load()
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

async function remove(file: LibraryFile) {
  confirming.value = null
  busy.value = file.path
  actionError.value = ''
  try {
    await deleteLibraryFile(props.projectId, file.path)
    files.value = files.value.filter((f) => f.path !== file.path)
    if (selectedPath.value === file.path) close()
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : t('work.library.deleteFailed')
  } finally {
    busy.value = ''
  }
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
  return [
    {
      id: 'library.upload',
      title: t('work.library.upload'),
      icon: 'mdi-upload',
      palette: false as const,
      loading: uploading.value,
      header: { primary: true, accent: true },
      run: () => picker.value?.click(),
    },
  ]
})

// ---- 摆出来 ------------------------------------------------------------------

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

function when(file: LibraryFile): string {
  return relTime(file.added_at ?? new Date(file.modified * 1000).toISOString())
}

function rowMeta(file: LibraryFile): string {
  return [file.added_by ?? t('work.library.unknownSource'), when(file), fmtBytes(file.bytes)].join(' · ')
}

// 行的身份（和 v-for 的 key 同义）：路径。同名不覆盖，所以路径唯一。写成具名函数而
// 不是模板里的箭头：模板里那个箭头参数没有类型来源，strict 下会报隐式 any。
function fileRowKey(row: unknown): string {
  return (row as LibraryFile).path
}

function read(file: LibraryFile) {
  return (asPdf: boolean) => libraryFileBytes(props.projectId, file.path, asPdf)
}
</script>

<template>
  <AppPage :title="selected && !mdAndUp ? selected.path : t('navigation.project.library')" width="full">
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
        <p v-if="loadError" role="alert" class="t-body c-danger">{{ loadError }}</p>
        <p v-if="actionError" role="alert" class="t-body c-danger">{{ actionError }}</p>

        <div v-if="files.length" class="library__tools">
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

        <div
          v-if="loading && !files.length"
          class="py-8 text-center"
          role="status"
          :aria-label="t('work.library.loading')"
        >
          <v-progress-circular indeterminate size="28" color="primary" />
        </div>

        <ul v-else-if="shown.length" class="library__rows">
          <!-- Long libraries go through VirtualList: past VIRTUAL_LIST_CONTENT_THRESHOLD
               (lib/virtualList.ts) only the rows in view stay mounted, below it this is
               the plain list it always was. `item-as="li"` keeps the ul > li structure
               in both paths — virtua only wraps each row; the row itself carries the
               flex layout, the buttons and the actions menu, so keyboard focus and the
               ⋯ menu work exactly as before. virtua owns the li, so the right-click
               handler sits on the row div it wraps. -->
          <VirtualList
            :items="shown"
            :item-key="fileRowKey"
            :scroll-parent="listEl"
            :threshold="VIRTUAL_LIST_CONTENT_THRESHOLD"
            :estimated-size="58"
            item-as="li"
            item-role="listitem"
          >
            <template #item="{ item: file }">
              <div
                class="library-row"
                :class="{ 'library-row--on': file.path === selectedPath }"
                @contextmenu="rowMenu.open(file.path, $event)"
              >
                <button type="button" class="library-row__open" @click="open(file)">
                  <v-icon :icon="KIND_ICONS[kindOf(file.path)]" size="20" class="library-row__icon" />
                  <span class="library-row__id">
                    <span class="library-row__name t-body">{{ file.path }}</span>
                    <span class="t-meta c-faint">{{ rowMeta(file) }}</span>
                  </span>
                </button>
                <AdaptiveMenu v-bind="rowMenu.bind(file.path)" :actions="fileActions(file)" :title="file.path">
                  <template #activator="{ props: menuProps }">
                    <BaseButton
                      v-bind="menuProps"
                      icon="mdi-dots-horizontal"
                      size="sm"
                      class="tap-target"
                      :loading="busy === file.path"
                      :aria-label="t('work.library.actionsOf', { name: file.path })"
                    />
                  </template>
                </AdaptiveMenu>
              </div>
            </template>
          </VirtualList>
        </ul>

        <!-- Read failure replaces the list: the server's own words plus one retry
             (401/403 switches to the no-access form, no retry). §3.10. -->
        <BaseLoadError
          v-else-if="loadError"
          :title="t('work.library.loadError')"
          :error="loadErrorDetail"
          :forbidden="loadForbidden"
          @retry="load"
        />
        <BaseEmptyState
          v-else-if="files.length"
          size="inline"
          align="center"
          class="library__empty"
          :title="t('work.library.noMatch')"
        />
        <BaseEmptyState v-else size="inline" align="center" class="library__empty" :title="t('work.library.empty')" />
      </section>

      <!-- 这一份：桌面上在右边一栏，手机上是整一页。 -->
      <section v-if="selected" class="library__detail" :class="{ 'library__detail--phone': !mdAndUp }">
        <header class="library__detail-head">
          <h2 v-if="mdAndUp" class="t-title library__detail-name">{{ selected.path }}</h2>
          <p class="t-meta c-faint library__detail-meta">
            {{ selected.added_by ?? t('work.library.unknownSource') }} · {{ when(selected) }} ·
            {{ fmtBytes(selected.bytes) }}
            <template v-if="selected.room">
              · {{ t('work.library.from') }}
              <router-link :to="{ name: 'workspace-topic', params: { projectId, topicId: selected.room.id } }">{{
                t('work.topic.quoted', { title: topicTitle(selected.room) })
              }}</router-link>
            </template>
          </p>
          <p v-if="selected.references || selected.replaced" class="t-meta c-faint library__detail-meta">
            <template v-if="selected.references">{{
              t('work.library.references', { n: selected.references })
            }}</template>
            <template v-if="selected.references && selected.replaced"> · </template>
            <template v-if="selected.replaced">{{
              t('work.library.replacedCount', { n: selected.replaced })
            }}</template>
          </p>
          <div v-if="mdAndUp" class="library__detail-actions">
            <BaseButton kind="primary" size="sm" prepend-icon="mdi-download-outline" @click="download(selected)">
              {{ t('work.library.download') }}
            </BaseButton>
            <BaseButton
              kind="secondary"
              size="sm"
              prepend-icon="mdi-file-replace-outline"
              :loading="busy === selected.path"
              @click="pickReplacement(selected)"
            >
              {{ t('work.library.replace') }}
            </BaseButton>
            <BaseButton size="sm" prepend-icon="mdi-close" :aria-label="t('work.library.close')" @click="close">
              {{ t('work.library.close') }}
            </BaseButton>
          </div>
        </header>
        <div class="library__preview">
          <FileBytesPreview :filename="selected.path" :source="`${selected.path}#${revision}`" :read="read(selected)" />
        </div>
        <div v-if="!mdAndUp" class="library__bar">
          <BaseButton kind="primary" block @click="download(selected)">{{ t('work.library.download') }}</BaseButton>
        </div>
      </section>
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
      :title="t('work.library.deleteTitle', { name: confirming?.path ?? '' })"
      :confirm-label="t('work.library.delete')"
      danger
      @update:model-value="confirming = null"
      @confirm="confirmRemove"
    >
      {{ t('work.library.deleteBody') }}
    </ConfirmDialog>
  </AppPage>
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

.library__detail {
  display: flex;
  flex: none;
  flex-direction: column;
  width: 480px;
  min-width: 0;
  min-height: 0;
  border-left: 1px solid var(--line);
}

.library__detail--phone {
  flex: 1 1 auto;
  width: auto;
  border-left: 0;
}

.library__detail-head {
  display: flex;
  flex: none;
  flex-direction: column;
  gap: 4px;
  padding: 16px 20px 12px;
  border-bottom: 1px solid var(--line);
}

.library__detail--phone .library__detail-head {
  padding: 10px 16px;
}

.library__detail-name {
  margin: 0;
  overflow-wrap: anywhere;
}

.library__detail-meta {
  margin: 0;
  overflow-wrap: anywhere;
}

.library__detail-meta a {
  color: var(--accent-ink);
  text-decoration: none;
}

.library__detail-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin: 4px 0 0 -8px;
}

.library__preview {
  flex: 1 1 auto;
  min-height: 0;
  background: var(--canvas);
}

.library__bar {
  flex: none;
  padding: 10px 16px calc(10px + env(safe-area-inset-bottom));
  border-top: 1px solid var(--line);
  background: var(--surface);
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
