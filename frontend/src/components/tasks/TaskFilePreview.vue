<script setup lang="ts">
// 题目页上点开的一份材料：和资料库同一个预览（`FileBytesPreview`），同一条大小上限
// （`lib/fileKind` 的 `PREVIEW_MAX_MB`）。字节怎么读由外面给（`read`）。
import { computed } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import FileBytesPreview from '@/components/common/FileBytesPreview.vue'
import { t } from '@/i18n'
import { fmtBytes } from '@/lib/changesTree'
import { PREVIEW_MAX_MB, tooLargeToPreview } from '@/lib/fileKind'

const props = defineProps<{
  /** 打开的那一份；null 是没打开。 */
  file: { name: string; size: number } | null
  read: (asPdf: boolean) => Promise<ArrayBuffer>
  /** 这一份正在下载。 */
  downloading?: boolean
}>()

const emit = defineEmits<{ close: []; download: [] }>()

const tooLarge = computed(() => (props.file ? tooLargeToPreview(props.file.size) : false))
</script>

<template>
  <v-dialog
    :model-value="file !== null"
    max-width="960"
    scrollable
    @update:model-value="(open: boolean) => !open && emit('close')"
  >
    <section v-if="file" class="tfp" :class="{ 'tfp--fit': tooLarge }" data-testid="task-file-preview">
      <header class="tfp__head">
        <h2 class="tfp__name t-title" data-user-content>{{ file.name }}</h2>
        <span class="tfp__size t-meta-read t-num">{{ fmtBytes(file.size) }}</span>
        <BaseButton
          kind="primary"
          size="sm"
          prepend-icon="mdi-download-outline"
          :loading="downloading"
          @click="emit('download')"
        >
          {{ t('tasks.attachments.download') }}
        </BaseButton>
        <BaseButton size="sm" prepend-icon="mdi-close" @click="emit('close')">
          {{ t('work.library.close') }}
        </BaseButton>
      </header>
      <div class="tfp__body">
        <p v-if="tooLarge" class="tfp__large t-body c-muted">
          {{ t('work.library.tooLargeToPreview', { mb: PREVIEW_MAX_MB }) }}
        </p>
        <FileBytesPreview v-else :filename="file.name" :source="file.name" :read="read" />
      </div>
    </section>
  </v-dialog>
</template>

<style scoped>
.tfp {
  display: flex;
  flex-direction: column;
  height: min(80vh, 900px);
  min-height: 0;
  border-radius: var(--radius-lg);
  background: var(--surface);
  overflow: hidden;
}

/* Too large to preview: only a sentence, so the box is as tall as that sentence. */
.tfp--fit {
  height: auto;
}

.tfp--fit .tfp__body {
  flex: none;
}

.tfp__head {
  display: flex;
  flex: none;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  padding: 12px 16px;
  border-bottom: 1px solid var(--line);
}

.tfp__name {
  flex: 1 1 auto;
  min-width: 0;
  margin: 0;
  overflow-wrap: anywhere;
}

.tfp__size {
  color: var(--muted);
}

.tfp__body {
  flex: 1 1 auto;
  min-height: 0;
  background: var(--canvas);
}

.tfp__large {
  margin: 0;
  padding: 24px;
  text-align: center;
}
</style>
