<script setup lang="ts">
// 团队的「资料库」：团队成员共用的文件；一个人名下的就是他自己的资料库，附给芝士的
// 文件和芝士交给他的文件都存在这里。这一页取数、上传、替换、挪动、删除，把看哪一份、
// 在哪一层记在地址上（`?file=`、`?dir=`），画面在同目录的 `LibraryView.vue`。
import type { LocationQueryRaw } from 'vue-router'
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

import { useCommands } from '@/commands'
import { t } from '@/i18n'
import { teamDataInjectionKey } from '@/keys'
import { libraryFileBytes, libraryRoom } from '@/lib/libraryApi'

const route = useRoute()
const router = useRouter()
const { mdAndUp } = useDisplay()
const teamData = inject(teamDataInjectionKey, ref())

const teamId = computed(() => teamData.value?.id ?? 0)
// 同一个团队一直是同一个对象：组合函数按它来判断换没换资料库。
const shelf = computed<LibraryShelf>(() => ({ team: teamId.value }))

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
    header: { primary: true, accent: true },
    run: () => view.value?.pickFiles(),
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
