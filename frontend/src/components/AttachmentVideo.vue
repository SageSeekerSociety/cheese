<script setup lang="ts">
import { computed, inject, onBeforeUnmount, ref, watch } from 'vue'

import BaseButton from './base/BaseButton.vue'

import { t } from '@/i18n'
import { ATTACHMENT_SOURCE, NO_ATTACHMENT_SOURCE } from '@/lib/attachmentSource'

const props = defineProps<{ topicId: string | null; path: string }>()
const emit = defineEmits<{ download: [] }>()
const source = inject(ATTACHMENT_SOURCE, NO_ATTACHMENT_SOURCE)
const name = computed(() => props.path.split('/').pop() ?? props.path)
const url = ref('')
const loading = ref(false)
const failure = ref<'load' | 'play' | null>(null)
let generation = 0

function release() {
  if (url.value) URL.revokeObjectURL(url.value)
  url.value = ''
}

function reset() {
  generation += 1
  release()
  loading.value = false
  failure.value = null
}

// A video cannot send Authorization headers. Fetch only on request, then let
// the native player seek the complete blob (attachments remain capped at 10MB).
async function load() {
  reset()
  const mine = generation
  if (!props.topicId) {
    failure.value = 'load'
    return
  }
  loading.value = true
  try {
    const next = await source.mediaUrl(props.topicId, props.path)
    if (mine !== generation) {
      URL.revokeObjectURL(next)
      return
    }
    url.value = next
  } catch {
    if (mine === generation) failure.value = 'load'
  } finally {
    if (mine === generation) loading.value = false
  }
}

watch(() => [props.topicId, props.path], reset)
onBeforeUnmount(reset)
</script>

<template>
  <div class="attachment-video">
    <!-- eslint-disable-next-line vuejs-accessibility/media-has-caption -- user-uploaded videos have no supplied caption track; keep native controls and download available -->
    <video
      v-if="url && !failure"
      :src="url"
      controls
      playsinline
      preload="metadata"
      :aria-label="t('work.room.video.label', { name })"
      @error="failure = 'play'"
    />
    <div v-else class="attachment-video__placeholder">
      <p v-if="failure" role="status">
        {{ failure === 'load' ? t('work.room.video.loadFailed') : t('work.room.video.playFailed') }}
      </p>
      <BaseButton kind="ghost" :disabled="loading" prepend-icon="mdi-play-circle-outline" @click="load">
        {{ loading ? t('work.room.video.loading') : failure ? t('work.room.video.retry') : t('work.room.video.open') }}
      </BaseButton>
    </div>
    <BaseButton
      kind="ghost"
      prepend-icon="mdi-video-outline"
      append-icon="mdi-download-outline"
      class="attachment-video__download"
      :title="t('work.room.message.downloadFile', { name })"
      :aria-label="t('work.room.message.downloadFile', { name })"
      @click="emit('download')"
    >
      <span class="text-truncate">{{ name }}</span>
    </BaseButton>
  </div>
</template>

<style scoped>
.attachment-video {
  width: min(360px, 100%);
  min-width: 0;
}
.attachment-video video,
.attachment-video__placeholder {
  display: block;
  width: 100%;
  aspect-ratio: 16 / 9;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--fill);
}
.attachment-video video {
  object-fit: contain;
}
.attachment-video__placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 12px;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
.attachment-video__placeholder p {
  margin: 0;
}
.attachment-video__download {
  max-width: 100%;
  margin-top: 4px;
}
</style>
