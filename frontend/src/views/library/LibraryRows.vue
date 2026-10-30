<script setup lang="ts">
// 资料库里那一列的行：一个文件夹、一份文档、一份文件各长什么样，每行的 ⋯ 里有什么。
//
// 只摆，不知道服务端在哪：数据、右栏开着的那一份、哪一行正忙都由页面给（props），点开、
// 下载、替换、改名、版本、删除都往上报（emits）。右键弹的就是那一行的 ⋯ 菜单，所以单
// 开菜单的那点状态也收在这里。
import type { ListedDocument } from '@/api/projectDocuments'
import type { MenuAction } from '@/components/common/menuAction'
import type { LibraryRow } from '@/composables/useLibraryPages'
import type { ProjectMemberRow } from '@/cx_types'
import type { DocAuthor } from '@/lib/docAuthors'
import type { LibraryFile, LibraryFolder } from '@/lib/libraryApi'

import { useRowMenu } from '@/composables/useRowMenu'

import BaseButton from '@/components/base/BaseButton.vue'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import VirtualList from '@/components/common/VirtualList.vue'
import DocAuthors from '@/components/library/DocAuthors.vue'
import { t } from '@/i18n'
import { fmtBytes } from '@/lib/changesTree'
import { docAuthors } from '@/lib/docAuthors'
import { leafOf } from '@/lib/libraryFolders'
import { KIND_ICONS, kindOf } from '@/lib/libraryKinds'
import { relTime } from '@/lib/relTime'
import { VIRTUAL_LIST_CONTENT_THRESHOLD } from '@/lib/virtualList'

const props = defineProps<{
  rows: LibraryRow[]
  /** 这一列的滚动容器：滚到离底不远就取下一页，行数过门槛就交给 VirtualList。 */
  scroller: HTMLElement | null
  /** 右栏里开着的那一份的路径；没有是 `''`。 */
  selectedPath: string
  /** 正忙的那一份（路径）：菜单里的「删除」转着圈；没有是 `''`。 */
  busy: string
  /** 一层一层地看（行上写最后一层的名字），还是一搜一筛（写完整路径）。 */
  browsing: boolean
  /** 文档那一行写「谁什么时候改的」要用的名册和队友名。 */
  members: ProjectMemberRow[]
  agentName: string
}>()

const emit = defineEmits<{
  openFile: [file: LibraryFile]
  openFolder: [path: string]
  openDoc: [id: string]
  download: [file: LibraryFile]
  replace: [file: LibraryFile]
  rename: [path: string]
  versions: [file: LibraryFile]
  /** 人点了「删除」：页面先问一句，答应了才真的扔。 */
  delete: [target: LibraryFile | LibraryFolder]
  deleteDoc: [doc: ListedDocument]
}>()

const rowMenu = useRowMenu<string>()

// 这一行写什么名字：一层一层看时写最后一层，搜出来的写完整路径。
function fileName(file: LibraryFile): string {
  return props.browsing ? leafOf(file.path) : file.path
}

function rowMeta(file: LibraryFile): string {
  return [file.added_by ?? t('work.library.unknownSource'), relTime(file.added_at), fmtBytes(file.bytes)].join(' · ')
}

function folderMeta(folder: LibraryFolder): string {
  return t(folder.readonly ? 'work.library.readonlyFolderMeta' : 'work.library.folderMeta', {
    n: folder.count,
    when: relTime(new Date(folder.modified * 1000).toISOString()),
  })
}

function docName(doc: { title: string | null }): string {
  return doc.title || t('work.room.doc.untitled')
}

function authorsOf(doc: ListedDocument): DocAuthor[] {
  return docAuthors(doc, props.members, props.agentName)
}

function folderActions(folder: LibraryFolder): MenuAction[] {
  return [
    {
      key: 'move',
      label: t('work.library.move'),
      icon: 'mdi-folder-move-outline',
      onSelect: () => emit('rename', folder.path),
    },
    {
      key: 'delete',
      label: t('work.library.delete'),
      icon: 'mdi-delete-outline',
      danger: true,
      loading: props.busy === folder.path,
      onSelect: () => emit('delete', folder),
    },
  ]
}

function fileActions(file: LibraryFile): MenuAction[] {
  const download: MenuAction = {
    key: 'download',
    label: t('work.library.download'),
    icon: 'mdi-download-outline',
    onSelect: () => emit('download', file),
  }
  const versions: MenuAction = {
    key: 'versions',
    label: t('work.library.versions'),
    icon: 'mdi-history',
    onSelect: () => emit('versions', file),
  }
  // 题目的材料只能读：换、挪、删都是出题人在题目那边的事。
  if (file.readonly) return [download, versions]
  return [
    download,
    {
      key: 'replace',
      label: t('work.library.replace'),
      icon: 'mdi-file-replace-outline',
      onSelect: () => emit('replace', file),
    },
    {
      key: 'move',
      label: t('work.library.move'),
      icon: 'mdi-folder-move-outline',
      onSelect: () => emit('rename', file.path),
    },
    versions,
    {
      key: 'delete',
      label: t('work.library.delete'),
      icon: 'mdi-delete-outline',
      danger: true,
      loading: props.busy === file.path,
      onSelect: () => emit('delete', file),
    },
  ]
}

function docActions(doc: ListedDocument): MenuAction[] {
  return [
    {
      key: 'delete',
      label: t('work.library.delete'),
      icon: 'mdi-delete-outline',
      danger: true,
      onSelect: () => emit('deleteDoc', doc),
    },
  ]
}

// 行的身份（和 v-for 的 key 同义）：路径。同名不覆盖，所以路径唯一。写成具名函数而
// 不是模板里的箭头：模板里那个箭头参数没有类型来源，strict 下会报隐式 any。
function entryKey(row: unknown): string {
  return (row as LibraryRow).key
}
</script>

<template>
  <ul class="library__rows">
    <!-- Long libraries go through VirtualList: past VIRTUAL_LIST_CONTENT_THRESHOLD
         (lib/virtualList.ts) only the rows in view stay mounted, below it this is
         the plain list it always was. `item-as="li"` keeps the ul > li structure
         in both paths — virtua only wraps each row; the row itself carries the
         flex layout, the buttons and the actions menu, so keyboard focus and the
         ⋯ menu work exactly as before. virtua owns the li, so the right-click
         handler sits on the row div it wraps. -->
    <VirtualList
      :items="props.rows"
      :item-key="entryKey"
      :scroll-parent="props.scroller"
      :threshold="VIRTUAL_LIST_CONTENT_THRESHOLD"
      :estimated-size="58"
      item-as="li"
      item-role="listitem"
    >
      <template #item="{ item }">
        <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- contextmenu bubbles here from the row's focused controls (the keyboard's Menu key fires it too); the row's ⋯ button opens the same menu -->
        <div v-if="item.folder" class="library-row" @contextmenu="rowMenu.open(item.key, $event)">
          <button type="button" class="library-row__open" @click="emit('openFolder', item.folder.path)">
            <v-icon icon="mdi-folder-outline" size="20" class="library-row__icon" />
            <span class="library-row__id">
              <span class="library-row__name t-body" data-user-content>{{ item.folder.name }}</span>
              <span class="t-meta c-faint">{{ folderMeta(item.folder) }}</span>
            </span>
          </button>
          <AdaptiveMenu
            v-if="!item.folder.readonly"
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
                :loading="props.busy === item.folder.path"
                :aria-label="t('work.library.actionsOf', { name: item.folder.name })"
              />
            </template>
          </AdaptiveMenu>
        </div>
        <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- contextmenu bubbles here from the row's focused controls (the keyboard's Menu key fires it too); the row's ⋯ button opens the same menu -->
        <div v-else-if="item.doc" class="library-row" @contextmenu="rowMenu.open(item.key, $event)">
          <button type="button" class="library-row__open" @click="emit('openDoc', item.doc.id)">
            <v-icon icon="mdi-file-document-edit-outline" size="20" class="library-row__icon" />
            <span class="library-row__id">
              <span class="library-row__name t-body" data-user-content>{{ docName(item.doc) }}</span>
              <span class="library-row__meta t-meta c-faint">
                <span>{{ t('work.library.docKind') }}</span>
                <DocAuthors :authors="authorsOf(item.doc)" />
                <span>{{ relTime(item.doc.updated_at) }}</span>
              </span>
            </span>
          </button>
          <AdaptiveMenu v-bind="rowMenu.bind(item.key)" :actions="docActions(item.doc)" :title="docName(item.doc)">
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
        <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- contextmenu bubbles here from the row's focused controls (the keyboard's Menu key fires it too); the row's ⋯ button opens the same menu -->
        <div
          v-else-if="item.file"
          class="library-row"
          :class="{ 'library-row--on': item.file.path === props.selectedPath }"
          @contextmenu="rowMenu.open(item.key, $event)"
        >
          <button type="button" class="library-row__open" @click="emit('openFile', item.file)">
            <v-icon :icon="KIND_ICONS[kindOf(item.file.path)]" size="20" class="library-row__icon" />
            <span class="library-row__id">
              <span class="library-row__name t-body" data-user-content>{{ fileName(item.file) }}</span>
              <span class="t-meta c-faint">{{ rowMeta(item.file) }}</span>
            </span>
          </button>
          <AdaptiveMenu v-bind="rowMenu.bind(item.key)" :actions="fileActions(item.file)" :title="fileName(item.file)">
            <template #activator="{ props: menuProps }">
              <BaseButton
                v-bind="menuProps"
                icon="mdi-dots-horizontal"
                size="sm"
                class="tap-target"
                :loading="props.busy === item.file.path"
                :aria-label="t('work.library.actionsOf', { name: fileName(item.file) })"
              />
            </template>
          </AdaptiveMenu>
        </div>
      </template>
    </VirtualList>
  </ul>
</template>

<style scoped>
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

/* 文档那一行的小字：哪种东西、作者、最后一次改动的时间，各占一格，靠间距分开。 */
.library-row__meta {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.library-row__name {
  color: var(--ink);
  overflow-wrap: anywhere;
}
</style>
