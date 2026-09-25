<script setup lang="ts">
// 一份房间文件保存过的每一版：谁、什么时候、从哪条路存的、说了改了什么。
//
// 这是草稿历史，不是交付记录。「恢复」把那一版的内容存成最新一版，之前的每一版照旧
// 留着；已经交出去、被采纳的那几版在产物页上，这里碰不到。

import type { RoomFileRevision } from '../../../api'

import { onMounted, ref, watch } from 'vue'

import { downloadRoomFileRevision, restoreRoomFileRevision, roomFileRevisions } from '../../../api'

const props = defineProps<{ topicId: string; path: string; version?: string | null }>()
const emit = defineEmits<{ (e: 'restored', revision: RoomFileRevision): void }>()

const rows = ref<RoomFileRevision[]>([])
const loading = ref(false)
const error = ref('')
const busy = ref<string | null>(null)
const confirming = ref<RoomFileRevision | null>(null)

const SOURCE_LABEL: Record<RoomFileRevision['source'], string> = {
  baseline: '最初的样子',
  upload: '上传',
  ai: '芝士修改',
  editor: '在线编辑保存',
  restore: '恢复',
  scheduled: '定时任务',
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    rows.value = (await roomFileRevisions(props.topicId, props.path)).data
  } catch (e) {
    error.value = e instanceof Error ? e.message : '读不到历史'
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
    error.value = e instanceof Error ? e.message : '恢复失败'
  } finally {
    busy.value = null
  }
}

async function download(row: RoomFileRevision) {
  try {
    await downloadRoomFileRevision(props.topicId, row)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '下载失败'
  }
}

function when(iso: string) {
  return new Date(iso).toLocaleString('zh-CN', { hour12: false })
}

onMounted(load)
watch(() => [props.path, props.version], load)
defineExpose({ reload: load })
</script>

<template>
  <div class="rh" data-testid="room-file-history">
    <div class="rh__title">
      保存历史
      <span class="t-meta">每次保存留一版，可以取回任何一版</span>
    </div>
    <v-alert v-if="error" type="warning" density="compact" class="my-2">{{ error }}</v-alert>
    <div v-if="loading && !rows.length" class="t-meta py-4 text-center">正在读取…</div>
    <div v-else-if="!rows.length" class="t-meta py-4 text-center">还没有保存记录。下一次保存起，这里会留下每一版。</div>
    <ol v-else class="rh__list">
      <li v-for="(row, i) in rows" :key="row.id" class="rh__row" :data-seq="row.seq">
        <div class="rh__head">
          <strong>第 {{ row.seq }} 版</strong>
          <v-chip v-if="i === 0" size="x-small" color="primary" variant="tonal">当前</v-chip>
          <span class="t-meta">{{ SOURCE_LABEL[row.source] ?? row.source }}</span>
          <span v-if="row.author" class="t-meta">· {{ row.author }}</span>
        </div>
        <div class="t-meta">{{ when(row.created_at) }}</div>
        <div v-if="row.note" class="rh__note">{{ row.note }}</div>
        <div class="rh__actions">
          <v-btn size="x-small" variant="text" prepend-icon="mdi-download" @click="download(row)">下载这一版</v-btn>
          <v-btn
            v-if="i !== 0"
            size="x-small"
            variant="text"
            color="primary"
            prepend-icon="mdi-restore"
            :loading="busy === row.id"
            @click="confirming = row"
          >
            恢复到这一版
          </v-btn>
        </div>
        <div v-if="confirming?.id === row.id" class="rh__confirm">
          把文件恢复成第 {{ row.seq }} 版的内容？现在这一版不会丢，仍在历史里。
          <div class="mt-1">
            <v-btn size="x-small" color="primary" variant="flat" @click="restore(row)">恢复</v-btn>
            <v-btn size="x-small" variant="text" @click="confirming = null">取消</v-btn>
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
