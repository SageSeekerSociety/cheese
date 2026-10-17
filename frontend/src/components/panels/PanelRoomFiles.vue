<script setup lang="ts">
// 「文件」那一格（#3828）：这段对话里的文件——发进来的附件、文件区里的文件（芝士交付
// 的、定时任务交回的、新建或复制来编辑的），和芝士写的文档。它们默认只属于这段对话，
// 保存到资料库之后，项目里的人在资料库里才找得到。
//
// 一层一层看，像文件夹：定时任务往「runs/2026-09-14/」这样的地方写结果，平铺出来只会
// 把日期那一层当成名字的一部分。一行一份：名字，谁、什么时候放进来的，以及它在不在资料
// 库里。点一份在面板里打开。数据和动作都从外面来（`RoomFilesPanelHost.vue`）。
import type { ConversationFile } from '@/composables/useConversationFiles'

import { computed, ref, watch } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import { t } from '@/i18n'
import { isAgentHandle } from '@/lib/authorship'
import { KIND_ICONS, kindOf } from '@/lib/libraryKinds'
import { relTime } from '@/lib/relTime'

const props = defineProps<{
  rows: ConversationFile[]
  /** handle → 名字，署名用。 */
  names: Record<string, string>
  loading: boolean
  error: string | null
  /** 正在保存到资料库的那几份，按钮转成进行中。 */
  keeping: ReadonlySet<string>
  /** 文件区的清单不是全部。 */
  truncated?: boolean
}>()

const emit = defineEmits<{
  open: [row: ConversationFile]
  keep: [row: ConversationFile]
  retry: []
}>()

/** 这一层里的一个文件夹：它的路径、名字，和它底下有几份、最近一份是什么时候。 */
interface FolderRow {
  path: string
  name: string
  count: number
  added_at: string
}

// 看哪一层。不写进地址：刷新回到最上层。
const here = ref('')
watch(
  () => props.rows,
  (rows) => {
    // 清单一换（重新读回来），脚下这一层可能已经不在了。
    if (here.value && !rows.some((row) => row.folder === here.value || row.folder.startsWith(`${here.value}/`)))
      here.value = ''
  }
)

/** 这一层有什么：文件夹在前，其余在后，各自按最近的排。文件夹是从每一份的路径数出来的。 */
const listing = computed(() => {
  const prefix = here.value ? `${here.value}/` : ''
  const folders = new Map<string, FolderRow>()
  const items: ConversationFile[] = []
  for (const row of props.rows) {
    if (row.folder === here.value) {
      items.push(row)
      continue
    }
    if (prefix && !row.folder.startsWith(prefix)) continue
    if (!prefix && !row.folder) continue
    const rest = row.folder.slice(prefix.length)
    const name = rest.split('/')[0]
    const path = `${prefix}${name}`
    const found = folders.get(path)
    if (!found) folders.set(path, { path, name, count: 1, added_at: row.added_at })
    else {
      found.count += 1
      if (row.added_at > found.added_at) found.added_at = row.added_at
    }
  }
  return {
    folders: [...folders.values()].sort((a, b) => b.added_at.localeCompare(a.added_at)),
    items,
  }
})

/** 从最上层到脚下这一层，一层一个名字。 */
const crumbs = computed(() => {
  const parts = here.value ? here.value.split('/') : []
  return parts.map((part, index) => ({ name: part, path: parts.slice(0, index + 1).join('/') }))
})

function icon(row: ConversationFile): string {
  return row.kind === 'document' ? 'mdi-file-document-outline' : KIND_ICONS[kindOf(row.name)]
}

/** 谁放进来的。名册上认不出的队友照默认名写，说不出是谁的照实写「来源未知」。 */
function who(row: ConversationFile): string {
  if (!row.added_by) return t('work.library.unknownSource')
  const known = props.names[row.added_by]
  if (known) return known
  return isAgentHandle(row.added_by) ? t('work.room.defaultAgentName') : row.added_by
}
</script>

<template>
  <section class="room-files-panel" :aria-label="t('work.room.tabs.files')">
    <BaseLoadError v-if="error" :error="error" @retry="emit('retry')" />
    <template v-else>
      <nav v-if="here" class="room-files-panel__crumbs" :aria-label="t('files.room.crumbsLabel')">
        <button type="button" class="room-files-panel__crumb" @click="here = ''">
          {{ t('files.room.crumbsRoot') }}
        </button>
        <template v-for="crumb in crumbs" :key="crumb.path">
          <span class="room-files-panel__crumb-sep" aria-hidden="true">/</span>
          <button
            type="button"
            class="room-files-panel__crumb"
            :aria-current="crumb.path === here ? 'location' : undefined"
            @click="here = crumb.path"
          >
            {{ crumb.name }}
          </button>
        </template>
      </nav>
      <ul v-if="listing.folders.length || listing.items.length" class="room-files-panel__rows">
        <li v-for="folder in listing.folders" :key="`folder:${folder.path}`" class="room-files-panel__row">
          <button type="button" class="room-files-panel__open" @click="here = folder.path">
            <v-icon icon="mdi-folder-outline" size="20" class="room-files-panel__icon" />
            <span class="room-files-panel__id">
              <span class="room-files-panel__name t-body">{{ folder.name }}</span>
              <span class="room-files-panel__meta t-meta c-muted">
                <span>{{ t('work.room.files.folderCount', { n: folder.count }, folder.count) }}</span>
                <span>{{ relTime(folder.added_at) }}</span>
              </span>
            </span>
          </button>
        </li>
        <li v-for="row in listing.items" :key="`${row.kind}:${row.id}`" class="room-files-panel__row">
          <button type="button" class="room-files-panel__open" @click="emit('open', row)">
            <v-icon :icon="icon(row)" size="20" class="room-files-panel__icon" />
            <span class="room-files-panel__id">
              <span class="room-files-panel__name t-body">{{ row.name }}</span>
              <span class="room-files-panel__meta t-meta c-muted">
                <span>{{ who(row) }}</span>
                <span>{{ relTime(row.added_at) }}</span>
              </span>
            </span>
          </button>
          <span v-if="row.in_library" class="room-files-panel__kept t-meta c-muted">
            <v-icon icon="mdi-check" size="16" />
            {{ t('work.room.files.inLibrary') }}
          </span>
          <BaseButton v-else size="sm" :loading="keeping.has(`${row.kind}:${row.id}`)" @click="emit('keep', row)">
            {{ t('work.room.files.keep') }}
          </BaseButton>
        </li>
      </ul>
      <BaseEmptyState
        v-else-if="!loading"
        size="inline"
        align="center"
        class="room-files-panel__empty"
        :title="t('work.room.files.empty')"
      />
      <p v-if="truncated" class="t-meta c-muted room-files-panel__truncated">
        {{ t('files.room.truncated', { n: rows.length }) }}
      </p>
    </template>
  </section>
</template>

<style scoped>
.room-files-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 0;
  padding: 12px 16px 24px;
  overflow-y: auto;
}

.room-files-panel__crumbs {
  display: flex;
  flex-wrap: wrap;
  gap: 2px;
  align-items: center;
}

.room-files-panel__crumb {
  padding: 2px 6px;
  font: inherit;
  color: var(--muted);
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: var(--radius-sm);
}

.room-files-panel__crumb:hover {
  color: var(--ink);
}

.room-files-panel__crumb[aria-current='location'] {
  font-weight: 600;
  color: var(--ink);
}

.room-files-panel__crumb-sep {
  color: var(--faint);
}

.room-files-panel__rows {
  display: flex;
  flex-direction: column;
  padding: 0;
  margin: 0;
  list-style: none;
}

.room-files-panel__row {
  display: flex;
  gap: 12px;
  align-items: center;
  padding-right: 4px;
  border-bottom: 1px solid var(--line);
}

.room-files-panel__open {
  display: flex;
  flex: 1 1 auto;
  gap: 12px;
  align-items: center;
  min-width: 0;
  padding: 10px 8px;
  font: inherit;
  color: inherit;
  text-align: left;
  cursor: pointer;
  background: none;
  border: 0;
  border-radius: var(--radius-md);
}

.room-files-panel__open:hover {
  background: var(--fill);
}

.room-files-panel__icon {
  flex: 0 0 auto;
  color: var(--faint);
}

.room-files-panel__id {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.room-files-panel__name {
  overflow: hidden;
  color: var(--ink);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.room-files-panel__meta {
  display: flex;
  gap: 12px;
}

.room-files-panel__kept {
  display: inline-flex;
  flex: 0 0 auto;
  gap: 4px;
  align-items: center;
}

.room-files-panel__empty {
  margin-top: 48px;
}

.room-files-panel__truncated {
  margin: 0;
}
</style>
