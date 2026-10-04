<script setup lang="ts">
// 一份房间文件保存过的每一版：谁、什么时候、从哪条路存的、说了改了什么。
//
// 这是草稿历史，不是交付记录。「恢复」把那一版的内容存成最新一版，之前的每一版照旧
// 留着；已经交出去、被采纳的那几版在产物页上，这里碰不到。

import type { RoomFileRevision } from '../../../api'

import { onMounted, ref, watch } from 'vue'

import { downloadRoomFileRevision, restoreRoomFileRevision, roomFileRevisions } from '../../../api'
import i18n, { t } from '../../../i18n'

import BaseButton from '@/components/base/BaseButton.vue'
import UserRef from '@/components/common/UserRefLink.vue'

const props = defineProps<{ topicId: string; path: string; version?: string | null }>()
const emit = defineEmits<{ (e: 'restored', revision: RoomFileRevision): void }>()

const rows = ref<RoomFileRevision[]>([])
const loading = ref(false)
const error = ref('')
const busy = ref<string | null>(null)
const confirming = ref<RoomFileRevision | null>(null)

const SOURCES = new Set<string>(['baseline', 'upload', 'ai', 'editor', 'restore', 'scheduled'])
function sourceLabel(source: RoomFileRevision['source']): string {
  return SOURCES.has(source) ? t(`work.room.fileHistory.source.${source}`) : source
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    rows.value = (await roomFileRevisions(props.topicId, props.path)).data
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('work.room.fileHistory.loadFailed')
  } finally {
    loading.value = false
  }
}

async function restore(row: RoomFileRevision) {
  busy.value = row.id
  error.value = ''
  try {
    const made = await restoreRoomFileRevision(props.topicId, row.id)
    confirming.value = null
    emit('restored', made)
    await load()
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('work.room.fileHistory.restoreFailed')
  } finally {
    busy.value = null
  }
}

async function download(row: RoomFileRevision) {
  try {
    await downloadRoomFileRevision(props.topicId, row)
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('work.room.fileHistory.downloadFailed')
  }
}

function when(iso: string) {
  return new Date(iso).toLocaleString(i18n.global.locale.value, { hour12: false })
}

onMounted(load)
watch(() => [props.path, props.version], load)
defineExpose({ reload: load })
</script>

<template>
  <div class="rh" data-testid="room-file-history">
    <div class="rh__title">
      {{ t('work.room.fileHistory.title') }}
      <span class="t-meta">{{ t('work.room.fileHistory.hint') }}</span>
    </div>
    <v-alert v-if="error" type="warning" density="compact" class="my-2">{{ error }}</v-alert>
    <div v-if="loading && !rows.length" class="t-meta py-4 text-center">{{ t('work.room.fileHistory.loading') }}</div>
    <div v-else-if="!rows.length" class="t-meta py-4 text-center">
      {{ t('work.room.fileHistory.empty') }}
    </div>
    <ol v-else class="rh__list">
      <li v-for="(row, i) in rows" :key="row.id" class="rh__row" :data-seq="row.seq">
        <div class="rh__head">
          <strong>{{ t('work.room.fileHistory.version', { seq: row.seq }) }}</strong>
          <v-chip v-if="i === 0" size="x-small" color="primary" variant="tonal">{{
            t('work.room.fileHistory.current')
          }}</v-chip>
          <span class="t-meta">{{ sourceLabel(row.source) }}</span>
          <span v-if="row.author" class="t-meta">· <UserRef :handle="row.author" /></span>
          <span v-else-if="row.author_kind === 'agent'" class="t-meta">· {{ t('work.room.defaultAgentName') }}</span>
        </div>
        <div class="t-meta">{{ when(row.created_at) }}</div>
        <div v-if="row.note" class="rh__note">{{ row.note }}</div>
        <div class="rh__actions">
          <BaseButton kind="ghost" size="sm" prepend-icon="mdi-download" @click="download(row)">{{
            t('work.room.fileHistory.downloadVersion')
          }}</BaseButton>
          <BaseButton
            v-if="i !== 0"
            kind="ghost"
            size="sm"
            prepend-icon="mdi-restore"
            :loading="busy === row.id"
            @click="confirming = row"
          >
            {{ t('work.room.fileHistory.restoreVersion') }}
          </BaseButton>
        </div>
        <div v-if="confirming?.id === row.id" class="rh__confirm">
          {{ t('work.room.fileHistory.restoreConfirm', { seq: row.seq }) }}
          <div class="mt-1">
            <BaseButton kind="primary" size="sm" @click="restore(row)">{{
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
