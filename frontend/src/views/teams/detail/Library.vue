<script setup lang="ts">
// 团队的「资料库」：团队成员共用的文档和文件；一个人名下的就是他自己的资料库，附给芝士
// 的文件和芝士交给他的文件都存在这里。和项目的资料库一样，文档和文件在同一张表里。
// 这一页取数、新建文档、上传、替换、挪动、删除，把看哪一份、在哪一层记在地址上
// （`?file=`、`?dir=`），文档整页打开（`TeamsDocument`）；画面在同目录的 `LibraryView.vue`。
import type { LocationQueryRaw } from 'vue-router'
import type { ListedDocument } from '@/api/projectDocuments'
import type { ProjectMemberRow } from '@/cx_types'
import type { LibraryFile, LibraryShelf } from '@/lib/libraryApi'
import type { Kind } from '@/lib/libraryKinds'

import { computed, inject, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'

import { useLibraryNaming } from '@/composables/useLibraryNaming'
import { useLibraryPages } from '@/composables/useLibraryPages'
import { useLibrarySelection } from '@/composables/useLibrarySelection'
import { useLibraryTransfers } from '@/composables/useLibraryTransfers'
import { useLibraryVersions } from '@/composables/useLibraryVersions'

import LibraryView from './LibraryView.vue'

import { deleteDocument } from '@/api/projectDocuments'
import { createTeamDocument } from '@/api/teamDocuments'
import { TeamsApi } from '@/api/teams'
import { useCommands } from '@/commands'
import { t } from '@/i18n'
import { teamDataInjectionKey } from '@/keys'
import { libraryFileBytes, libraryRoom } from '@/lib/libraryApi'
import { useAgentContext } from '@/stores/agent'

const route = useRoute()
const router = useRouter()
const { mdAndUp } = useDisplay()
const teamData = inject(teamDataInjectionKey, ref())

const teamId = computed(() => teamData.value?.id ?? 0)
// 同一个团队一直是同一个对象：组合函数按它来判断换没换资料库。
const shelf = computed<LibraryShelf>(() => ({ team: teamId.value }))

// 芝士读到的「当前页面」是这个资料库：自己的能按名字读，团队的只看得到有哪些文件。
useAgentContext(() =>
  teamData.value
    ? {
        page: { kind: 'library', id: teamData.value.id },
        label: teamData.value.personal
          ? t('tasks.assistant.page.library')
          : t('tasks.assistant.page.teamLibrary', { name: teamData.value.name }),
      }
    : null
)

const view = ref<InstanceType<typeof LibraryView> | null>(null)
const scroller = computed(() => view.value?.listEl ?? null)

const actionError = ref('')
const revision = ref(0)
const query = ref('')
const kind = ref<Kind>('all')
const searching = computed(() => !!query.value.trim())
const browsing = computed(() => !searching.value && kind.value === 'all')
const dir = computed(() => (typeof route.query.dir === 'string' ? route.query.dir : ''))

const pages = useLibraryPages(
  () => shelf.value,
  () => ({ dir: browsing.value ? dir.value : '', q: query.value, kind: kind.value }),
  scroller
)
const anything = computed(() => !(browsing.value && !dir.value && pages.exhausted.value && !pages.rows.value.length))

function goTo(path: string) {
  const next: LocationQueryRaw = { ...route.query, dir: path || undefined }
  delete next.file
  void router.push({ query: next })
}

const { selectedPath, selected, open, close } = useLibrarySelection({
  shelf: () => shelf.value,
  entries: pages.rows,
  revision,
  mdAndUp,
  router,
  route,
})

const room = ref<{ used: number; total: number } | null>(null)
async function measure() {
  if (!teamId.value) return
  try {
    room.value = await libraryRoom(teamId.value)
  } catch {
    // 用量读不到不挡这一页：条不摆就是了。
    room.value = null
  }
}
watch(teamId, measure, { immediate: true })

function reset() {
  pages.reset()
  void measure()
}

// 文档行上写谁改的：团队成员的名字。
const members = ref<ProjectMemberRow[]>([])
watch(
  teamId,
  async (id) => {
    if (!id) return
    try {
      const roster = await TeamsApi.getMembers(id)
      members.value = roster.members.map((m) => ({
        user_handle: m.user.username,
        name: m.user.nickname,
        name_source: 'human',
      }))
    } catch {
      // 名字读不到，行上照旧写账号名。
      members.value = []
    }
  },
  { immediate: true }
)

function openDoc(id: string) {
  void router.push({ name: 'TeamsDocument', params: { handle: teamData.value?.handle ?? '', docId: id } })
}

const making = ref(false)
async function makeDoc() {
  if (!teamId.value) return
  making.value = true
  actionError.value = ''
  try {
    openDoc((await createTeamDocument(teamId.value)).id)
  } catch (cause) {
    actionError.value = cause instanceof Error ? cause.message : t('work.library.newDocFailed')
  } finally {
    making.value = false
  }
}

const confirmingDoc = ref<ListedDocument | null>(null)
async function removeDoc() {
  const doc = confirmingDoc.value
  confirmingDoc.value = null
  if (!doc) return
  actionError.value = ''
  try {
    await deleteDocument(doc.id)
    pages.drop((row) => row.doc?.id === doc.id)
  } catch (cause) {
    actionError.value = cause instanceof Error ? cause.message : t('work.library.deleteFailed')
  }
}

const transfers = useLibraryTransfers({
  shelf: () => shelf.value,
  dir: () => dir.value,
  reset,
  drop: (gone) => {
    pages.drop(gone)
    void measure()
  },
  opened: () => selectedPath.value,
  close,
  changed: () => (revision.value += 1),
  error: actionError,
})

const history = useLibraryVersions(
  () => shelf.value,
  () => transfers.versionsOf.value,
  transfers.onRestored
)

const naming = useLibraryNaming({
  shelf: () => shelf.value,
  dir: () => dir.value,
  goTo,
  opened: () => selectedPath.value,
  reopen: (path) => void router.replace({ query: { ...route.query, file: path } }),
  reload: async () => pages.reset(),
})

/** 还没开着哪一份时给详情栏的读法：它不会被调到。 */
const nothing = async () => new ArrayBuffer(0)

function read(file: LibraryFile) {
  return (asPdf: boolean) => libraryFileBytes(shelf.value, file.path, asPdf)
}

useCommands(() => [
  {
    id: 'teamLibrary.newFolder',
    title: t('work.library.newFolder'),
    icon: 'mdi-folder-plus-outline',
    palette: false as const,
    header: {},
    run: () => naming.start({ newFolder: true }),
  },
  {
    id: 'teamLibrary.upload',
    title: t('work.library.upload'),
    icon: 'mdi-upload',
    palette: false as const,
    loading: transfers.uploading.value,
    header: {},
    run: () => view.value?.pickFiles(),
  },
  {
    id: 'teamLibrary.newDoc',
    title: t('work.library.newDoc'),
    icon: 'mdi-plus',
    palette: false as const,
    loading: making.value,
    header: { primary: true, accent: true },
    run: () => void makeDoc(),
  },
])
</script>

<template>
  <LibraryView
    ref="view"
    v-model:query="query"
    v-model:kind="kind"
    :team="teamId"
    :personal="!!teamData?.personal"
    :room="room"
    :rows="pages.rows.value"
    :members="members"
    :confirming-doc="confirmingDoc"
    :loading="pages.loading.value"
    :load-failed="pages.loadFailed.value"
    :load-reason="pages.loadReason.value"
    :action-error="actionError"
    :dir="dir"
    :browsing="browsing"
    :anything="anything"
    :selected="selected"
    :selected-path="selectedPath"
    :busy="transfers.busy.value"
    :wide="mdAndUp"
    :revision="revision"
    :read="selected ? read(selected) : nothing"
    :versions-of="transfers.versionsOf.value"
    :versions="history.versions.value"
    :versions-loading="history.loading.value"
    :versions-failed="history.failed.value"
    :versions-error="history.actionError.value"
    :restoring="history.restoring.value"
    :replacing="transfers.replacing.value"
    :confirming="transfers.confirming.value"
    :naming="!!naming.pending.value"
    :naming-dialog="naming.dialog.value"
    :naming-busy="naming.moving.value"
    :naming-error="naming.error.value"
    @go="goTo"
    @open="open"
    @close="close"
    @open-doc="openDoc"
    @remove-doc="(doc: ListedDocument) => (confirmingDoc = doc)"
    @confirm-remove-doc="removeDoc"
    @cancel-remove-doc="confirmingDoc = null"
    @upload="(files: File[]) => transfers.upload(files)"
    @download="transfers.download"
    @replacement="(target: LibraryFile, file: File) => (transfers.replacing.value = { target, file })"
    @confirm-replace="transfers.replace"
    @cancel-replace="transfers.replacing.value = null"
    @rename="(path: string) => naming.start({ path })"
    @versions="(file: LibraryFile) => (transfers.versionsOf.value = file.path)"
    @close-versions="transfers.versionsOf.value = null"
    @retry-versions="history.load"
    @download-version="history.download"
    @restore-version="history.restore"
    @remove="(target) => (transfers.confirming.value = target)"
    @confirm-remove="transfers.confirmRemove"
    @cancel-remove="transfers.confirming.value = null"
    @close-naming="naming.pending.value = null"
    @submit-naming="naming.submit"
    @clear-filters="(query = ''), (kind = 'all')"
    @retry="reset"
  />
</template>
