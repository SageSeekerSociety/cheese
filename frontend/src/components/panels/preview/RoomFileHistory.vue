<script setup lang="ts">
// 一份房间文件保存过的每一版：谁、什么时候、从哪条路存的、说了改了什么。
//
// 这是草稿历史，不是交付记录。「恢复」把那一版的内容存成最新一版，之前的每一版照旧
// 留着；已经交出去、被采纳的那几版在产物页上，这里碰不到。

import type { RoomFileRevision } from '../../../api'
import type { RoomFileHistoryBundle } from '../../../composables/useRoomFileHistory'

import { ref } from 'vue'

import i18n, { t } from '../../../i18n'
import { userRefRoute } from '../../../lib/userRef'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import UserRef from '@/components/common/UserRef.vue'

// **只吃 props**：清单、下载、恢复都在 `composables/useRoomFileHistory.ts` 里
// （`components/work/PanelPreviewHost.vue` 调一次，编辑器和预览台共用那一份），这一
// 只只决定画成什么样。`components/panels/**` 下每个 SFC 都是「场景」，场景不取数。
const props = defineProps<{
  /** 这一份历史的取数（`composables/useRoomFileHistory.ts` 那一包）。 */
  fileHistory: RoomFileHistoryBundle
  /** 这一份属于哪个项目：人名 chip 去成员页时要用（和别处那颗 chip 同一个去处）。 */
  projectId?: string | null
}>()

const emit = defineEmits<{
  /** 点了一个人名：去他的主页这件事在会读路由的那一层做。 */
  (e: 'mention-click', handle: string): void
}>()

// 哪一版的「恢复」按开了（还要再确认一下）——纯界面状态，和取数无关。
const confirming = ref<RoomFileRevision | null>(null)

// 恢复是一条会改文件的长活：等它回来才收回那个确认框（失败了还留着，读者能再试一次）。
async function confirmRestore(row: RoomFileRevision) {
  if (await props.fileHistory.restore(row)) confirming.value = null
}

const SOURCES = new Set<string>(['baseline', 'upload', 'ai', 'editor', 'restore', 'scheduled'])
function sourceLabel(source: RoomFileRevision['source']): string {
  return SOURCES.has(source) ? t(`work.room.fileHistory.source.${source}`) : source
}

function when(iso: string) {
  return new Date(iso).toLocaleString(i18n.global.locale.value, { hour12: false })
}
</script>

<template>
  <div class="rh" data-testid="room-file-history">
    <div class="rh__title">
      {{ t('work.room.fileHistory.title') }}
      <span class="t-meta">{{ t('work.room.fileHistory.hint') }}</span>
    </div>
    <v-alert v-if="props.fileHistory.error.value" type="warning" density="compact" class="my-2">
      {{ props.fileHistory.error.value }}
    </v-alert>
    <div v-if="props.fileHistory.loading.value && !props.fileHistory.rows.value.length" class="t-meta py-4 text-center">
      {{ t('work.room.fileHistory.loading') }}
    </div>
    <BaseEmptyState
      v-else-if="!props.fileHistory.rows.value.length"
      size="inline"
      align="center"
      class="py-4"
      :title="t('work.room.fileHistory.empty')"
    />
    <ol v-else class="rh__list">
      <li v-for="(row, i) in props.fileHistory.rows.value" :key="row.id" class="rh__row" :data-seq="row.seq">
        <div class="rh__head">
          <strong>{{ t('work.room.fileHistory.version', { seq: row.seq }) }}</strong>
          <v-chip v-if="i === 0" size="x-small" color="primary" variant="tonal">{{
            t('work.room.fileHistory.current')
          }}</v-chip>
          <span class="t-meta">{{ sourceLabel(row.source) }}</span>
          <span v-if="row.author" class="t-meta">
            ·
            <UserRef
              :handle="row.author"
              :to="userRefRoute(row.author, props.projectId)"
              @navigate="emit('mention-click', row.author ?? '')"
            />
          </span>
          <span v-else-if="row.author_kind === 'agent'" class="t-meta">· {{ t('work.room.defaultAgentName') }}</span>
        </div>
        <div class="t-meta">{{ when(row.created_at) }}</div>
        <div v-if="row.note" class="rh__note">{{ row.note }}</div>
        <div class="rh__actions">
          <BaseButton kind="ghost" size="sm" prepend-icon="mdi-download" @click="props.fileHistory.download(row)">{{
            t('work.room.fileHistory.downloadVersion')
          }}</BaseButton>
          <BaseButton
            v-if="i !== 0"
            kind="ghost"
            size="sm"
            prepend-icon="mdi-restore"
            :loading="props.fileHistory.busy.value === row.id"
            @click="confirming = row"
          >
            {{ t('work.room.fileHistory.restoreVersion') }}
          </BaseButton>
        </div>
        <div v-if="confirming?.id === row.id" class="rh__confirm">
          {{ t('work.room.fileHistory.restoreConfirm', { seq: row.seq }) }}
          <div class="mt-1">
            <BaseButton kind="primary" size="sm" @click="confirmRestore(row)">{{
              t('work.room.fileHistory.restore')
            }}</BaseButton>
            <BaseButton kind="ghost" size="sm" @click="confirming = null">{{
              t('work.room.fileHistory.cancel')
            }}</BaseButton>
          </div>
        </div>
      </li>
    </ol>
  </div>
</template>

<style scoped>
.rh {
  padding: 12px 16px;
}
.rh__title {
  font-weight: 600;
  display: flex;
  gap: 8px;
  align-items: baseline;
}
.rh__list {
  list-style: none;
  padding: 0;
  margin: 8px 0 0;
}
.rh__row {
  padding: 8px 0;
  border-top: 1px solid var(--line);
}
.rh__head {
  display: flex;
  gap: 6px;
  align-items: center;
  flex-wrap: wrap;
}
.rh__note {
  margin-top: 4px;
  white-space: pre-wrap;
}
.rh__actions {
  margin-top: 4px;
}
.rh__confirm {
  margin-top: 6px;
  padding: 8px;
  border-radius: var(--radius-sm);
  background: var(--canvas);
}
</style>
