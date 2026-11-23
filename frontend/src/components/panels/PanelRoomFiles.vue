<script setup lang="ts">
// 「文件」那一格（#3828）：这段对话里的文件——发进来的附件、摆出来过的和人放进来的文件，
// 和芝士写的文档。它们默认只属于这段对话，保存到资料库之后，项目里的人在资料库里才找得到。
//
// 一行一样东西，新的在前，不按文件夹分：名字；它在哪个文件夹、谁、什么时候放进来的；以及
// 它在不在资料库里。点一份在面板里打开。数据和动作都从外面来（`RoomFilesPanelHost.vue`）。
import type { ConversationFile } from '@/composables/useConversationFiles'

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
      <ul v-if="rows.length" class="room-files-panel__rows">
        <li v-for="row in rows" :key="`${row.kind}:${row.id}`" class="room-files-panel__row">
          <button
            type="button"
            class="room-files-panel__open"
            :title="row.folder ? `${row.folder}/${row.name}` : row.name"
            @click="emit('open', row)"
          >
            <v-icon :icon="icon(row)" size="20" class="room-files-panel__icon" />
            <span class="room-files-panel__id">
              <span class="room-files-panel__name t-body">{{ row.name }}</span>
              <span class="room-files-panel__meta t-meta c-muted">
                <span v-if="row.folder" class="room-files-panel__folder">{{ row.folder }}</span>
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
  min-width: 0;
}

.room-files-panel__folder {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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
