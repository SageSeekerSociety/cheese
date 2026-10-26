<template>
  <ul class="ta">
    <li v-for="file in attachments" :key="file.name" class="ta__row" data-testid="task-attachment">
      <v-icon size="16" class="ta__icon">mdi-file-outline</v-icon>
      <!-- Opening is reading the bytes, so it follows canDownload like the download does. -->
      <button
        v-if="previewable && canDownload && file.size !== null"
        type="button"
        class="ta__name ta__open"
        @click="emit('open', file)"
      >
        {{ file.name }}
      </button>
      <span v-else class="ta__name">{{ file.name }}</span>
      <span v-if="file.size === null" class="ta__meta ta__gone">{{ t('tasks.versions.removed') }}</span>
      <template v-else>
        <span class="ta__meta t-num">{{ fmtBytes(file.size) }}</span>
        <!-- 看得见清单不等于拿得到文件。这一行由服务端的 canDownload 决定，
             前端不拿自己的角色去猜 —— 判据只有一份。 -->
        <BaseButton
          v-if="canDownload"
          kind="ghost"
          size="sm"
          prepend-icon="mdi-download"
          :loading="downloading === file.name"
          @click="emit('download', file)"
        >
          {{ t('tasks.attachments.download') }}
        </BaseButton>
        <span v-else-if="!lockedSaidAbove" class="ta__locked">{{ t('tasks.attachments.locked') }}</span>
      </template>
    </li>
  </ul>
</template>

<script setup lang="ts" generic="T extends { name: string; size: number | null }">
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'
import { fmtBytes } from '@/lib/changesTree'

/** 一道题的材料清单。清单和能不能下载都由外面给，这里只画和报「要下载 / 打开哪一个」。
 *  `size` 是 null 的一份已经删掉了（历史版本里那一版有过它），只剩名字。 */
withDefaults(
  defineProps<{
    attachments: T[]
    canDownload: boolean
    /** 正在下载的那一份（名字）。 */
    downloading?: string | null
    /** 点名字打开预览。 */
    previewable?: boolean
    /** 「开始做之后才能下载」已经在清单上方说过一次，每行不再重复。 */
    lockedSaidAbove?: boolean
  }>(),
  { downloading: null, previewable: false, lockedSaidAbove: false }
)

const emit = defineEmits<{ download: [file: T]; open: [file: T] }>()

const { t } = useI18n()
</script>

<style scoped>
.ta {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-width: 520px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.ta__row {
  display: flex;
  gap: 8px;
  align-items: center;
  min-height: 40px;
  padding: 4px 4px 4px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  font-size: 13px;
}

.ta__icon {
  color: var(--faint);
}

.ta__name {
  min-width: 0;
  overflow: hidden;
  color: var(--ink);
  white-space: nowrap;
  text-overflow: ellipsis;
}

/* A name that opens a preview reads as a link before it is hovered. */
.ta__open {
  padding: 0;
  border: 0;
  background: none;
  color: var(--accent-ink);
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.ta__open:hover {
  text-decoration: underline;
}

.ta__meta {
  flex: 1;
  color: var(--faint);
  font-size: 12px;
  white-space: nowrap;
}

.ta__gone {
  padding-right: 6px;
  color: var(--muted);
  font-size: 13px;
}

.ta__locked {
  padding-right: 6px;
  color: var(--muted);
  font-size: 12px;
  white-space: nowrap;
}
</style>
