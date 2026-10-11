<script setup lang="ts">
// 一个频道的文件：这个频道自己的东西都在这一页上——贴进来的截图、芝士交付的文档、
// 定时任务交回的结果。以前只有干活的那台机器和记得住名字的人看得见它们。
//
// 一页一份文件一行，最近改的在最前；点一份在右边一栏看内容，手机上它是整一页。
// 数据和动作都从外面来（`ChannelFiles.vue`）。
import type { RoomFile } from '@/api/roomFiles'

import { computed, ref, watch } from 'vue'
import { useDisplay } from 'vuetify'

import AppPage from '@/components/common/AppPage.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import FileBytesPreview from '@/components/common/FileBytesPreview.vue'
import { t } from '@/i18n'
import { fmtBytes } from '@/lib/changesTree'
import { KIND_ICONS, kindOf } from '@/lib/libraryKinds'
import { relTime } from '@/lib/relTime'

const props = defineProps<{
  files: RoomFile[]
  /** 清单不是全部：文件多到超过上限，只列了最近改的那些。 */
  truncated: boolean
  loading: boolean
  error: string | null
  read: (file: RoomFile, asPdf: boolean) => Promise<ArrayBuffer>
}>()

const emit = defineEmits<{
  (e: 'retry'): void
  (e: 'download', file: RoomFile): void
}>()

const { mdAndUp } = useDisplay()

// 看哪一份。地址上不留：这一页不是别人会转发的去处（要发给别人是「复制链接」拿到的
// 那个频道地址），刷新回到清单。
const selected = ref<RoomFile | null>(null)
watch(
  () => props.files,
  (rows) => {
    if (selected.value && !rows.some((row) => row.path === selected.value?.path)) selected.value = null
  }
)

/** 名字和它所在的文件夹分开写：一个频道里文件可能重名，路径才不会。 */
function name(file: RoomFile): string {
  return file.path.split('/').pop() ?? file.path
}

function folder(file: RoomFile): string {
  const cut = file.path.lastIndexOf('/')
  return cut < 0 ? '' : file.path.slice(0, cut)
}

function meta(file: RoomFile): string {
  return [relTime(file.modified_at), fmtBytes(file.bytes)].filter(Boolean).join(' · ')
}

const title = computed(() => (selected.value && !mdAndUp ? name(selected.value) : t('navigation.project.channelFiles')))

function readSelected(asPdf: boolean): Promise<ArrayBuffer> {
  return selected.value ? props.read(selected.value, asPdf) : Promise.resolve(new ArrayBuffer(0))
}
</script>

<template>
  <AppPage :title="title" width="full">
    <template #meta>{{ t('files.room.readOnly') }}</template>

    <div class="room-files">
      <!-- 清单：手机上看着一份文件时让出整页。 -->
      <section v-if="mdAndUp || !selected" class="room-files__list">
        <BaseLoadError
          v-if="error"
          :title="t('files.room.loadError')"
          :error="error"
          @retry="emit('retry')"
        />

        <div
          v-else-if="loading && !files.length"
          class="py-8 text-center"
          role="status"
          :aria-label="t('files.room.loading')"
        >
          <v-progress-circular indeterminate size="28" color="primary" />
        </div>

        <ul v-else-if="files.length" class="room-files__rows">
          <li v-for="file in files" :key="file.path" class="room-files__row" :class="{ 'room-files__row--on': selected?.path === file.path }">
            <button type="button" class="room-files__open" @click="selected = file">
              <v-icon :icon="KIND_ICONS[kindOf(file.path)]" size="20" class="room-files__icon" />
              <span class="room-files__id">
                <span class="room-files__name t-body">{{ name(file) }}</span>
                <span class="t-meta c-faint">{{ meta(file) }}</span>
                <span v-if="folder(file)" class="t-meta c-faint room-files__folder">{{ folder(file) }}</span>
              </span>
            </button>
          </li>
        </ul>

        <BaseEmptyState v-else size="inline" align="center" class="room-files__empty" :title="t('files.room.empty')" />

        <p v-if="truncated" class="t-meta c-faint room-files__truncated">
          {{ t('files.room.truncated', { n: files.length }) }}
        </p>
      </section>

      <!-- 这一份：桌面上在右边一栏，手机上是整一页。 -->
      <section v-if="selected" class="room-files__detail" :class="{ 'room-files__detail--phone': !mdAndUp }">
        <header class="room-files__head">
          <h2 v-if="mdAndUp" class="t-title room-files__detail-name">{{ name(selected) }}</h2>
          <p class="t-meta c-faint room-files__detail-meta">
            {{ meta(selected) }}
            <template v-if="folder(selected)"> · {{ folder(selected) }}</template>
          </p>
          <div v-if="mdAndUp" class="room-files__actions">
            <BaseButton kind="primary" size="sm" prepend-icon="mdi-download-outline" @click="emit('download', selected)">
              {{ t('work.library.download') }}
            </BaseButton>
            <BaseButton size="sm" prepend-icon="mdi-close" :aria-label="t('work.library.close')" @click="selected = null">
              {{ t('work.library.close') }}
            </BaseButton>
          </div>
        </header>
        <div class="room-files__preview">
          <FileBytesPreview
            :filename="selected.path"
            :source="selected.path"
            :read="readSelected"
          />
        </div>
        <div v-if="!mdAndUp" class="room-files__bar">
          <BaseButton kind="primary" block @click="emit('download', selected)">{{ t('work.library.download') }}</BaseButton>
        </div>
      </section>
    </div>
  </AppPage>
</template>

<style scoped>
.room-files {
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
}

.room-files__list {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
  padding: 20px 24px 32px;
  overflow-y: auto;
}

.room-files__rows {
  display: flex;
  flex-direction: column;
  padding: 0;
  margin: 0;
  list-style: none;
  border-top: 1px solid var(--line);
}

.room-files__row {
  display: flex;
  align-items: center;
  border-bottom: 1px solid var(--line);
}

.room-files__row--on {
  background: var(--fill);
}

.room-files__open {
  display: flex;
  flex: 1 1 auto;
  gap: 10px;
  align-items: center;
  min-width: 0;
  padding: 10px 12px;
  font: inherit;
  text-align: left;
  cursor: pointer;
  background: none;
  border: 0;
}

.room-files__open:hover {
  background: var(--fill);
}

.room-files__icon {
  flex: none;
  color: var(--muted);
}

.room-files__id {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.room-files__name {
  overflow-wrap: anywhere;
}

.room-files__folder {
  overflow-wrap: anywhere;
}

.room-files__empty {
  margin-top: 24px;
}

.room-files__detail {
  display: flex;
  flex: none;
  flex-direction: column;
  width: 480px;
  min-width: 0;
  min-height: 0;
  border-left: 1px solid var(--line);
}

.room-files__detail--phone {
  flex: 1 1 auto;
  width: auto;
  border-left: 0;
}

.room-files__head {
  display: flex;
  flex: none;
  flex-direction: column;
  gap: 4px;
  padding: 16px 20px 12px;
  border-bottom: 1px solid var(--line);
}

.room-files__detail--phone .room-files__head {
  padding: 10px 16px;
}

.room-files__detail-name {
  margin: 0;
  overflow-wrap: anywhere;
}

.room-files__detail-meta {
  margin: 0;
  overflow-wrap: anywhere;
}

.room-files__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin: 4px 0 0 -8px;
}

.room-files__preview {
  flex: 1 1 auto;
  min-height: 0;
  background: var(--canvas);
}

.room-files__bar {
  flex: none;
  padding: 10px 16px calc(10px + env(safe-area-inset-bottom));
  border-top: 1px solid var(--line);
  background: var(--surface);
}
</style>
