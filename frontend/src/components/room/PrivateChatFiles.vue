<script setup lang="ts">
// 私聊右侧面板的「文件」（#3828）：清单和频道那一格是同一个（`PanelRoomFiles`）；点一份
// 文件就在这一栏里看它，上面一条返回清单、下载。文档不在这里打开，交给页面（它有自己
// 的一页）。数据和动作都从外面来（`DmView.vue`）。
import type { ConversationFile } from '@/composables/useConversationFiles'

import BaseButton from '@/components/base/BaseButton.vue'
import FileBytesPreview from '@/components/common/FileBytesPreview.vue'
import PanelRoomFiles from '@/components/panels/PanelRoomFiles.vue'
import { t } from '@/i18n'

defineProps<{
  rows: ConversationFile[]
  names: Record<string, string>
  loading: boolean
  error: string | null
  keeping: ReadonlySet<string>
  truncated: boolean
  /** 正在看的那一份文件（路径）；空 = 看清单。 */
  viewing: string | null
  /** 那一份的字节：由页面去读。 */
  read: (asPdf: boolean) => Promise<ArrayBuffer>
}>()

const emit = defineEmits<{
  open: [row: ConversationFile]
  keep: [row: ConversationFile]
  retry: []
  back: []
  download: [path: string]
}>()

function nameOf(path: string): string {
  return path.split('/').pop() ?? path
}
</script>

<template>
  <section v-if="viewing" class="dm-file">
    <header class="dm-file__head">
      <BaseButton
        icon="mdi-arrow-left"
        size="sm"
        :title="t('work.room.files.backToList')"
        :aria-label="t('work.room.files.backToList')"
        @click="emit('back')"
      />
      <span class="dm-file__name t-body">{{ nameOf(viewing) }}</span>
      <BaseButton size="sm" prepend-icon="mdi-download-outline" @click="emit('download', viewing)">
        {{ t('work.library.download') }}
      </BaseButton>
    </header>
    <div class="dm-file__preview">
      <FileBytesPreview :filename="viewing" :source="viewing" :read="read" />
    </div>
  </section>
  <PanelRoomFiles
    v-else
    :rows="rows"
    :names="names"
    :loading="loading"
    :error="error"
    :keeping="keeping"
    :truncated="truncated"
    @open="emit('open', $event)"
    @keep="emit('keep', $event)"
    @retry="emit('retry')"
  />
</template>

<style scoped>
.dm-file {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-height: 0;
}

.dm-file__head {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 8px 12px;
  border-bottom: 1px solid var(--line);
}

.dm-file__name {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  color: var(--ink);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dm-file__preview {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
}
</style>
