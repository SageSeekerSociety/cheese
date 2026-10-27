<script setup lang="ts">
import { computed, inject, onBeforeUnmount, ref, watch } from 'vue'

import BaseButton from './base/BaseButton.vue'

import { t } from '@/i18n'
import { ATTACHMENT_SOURCE, NO_ATTACHMENT_SOURCE } from '@/lib/attachmentSource'

const props = defineProps<{ topicId: string | null; path: string; canOpen?: boolean; canLocate?: boolean }>()
const emit = defineEmits<{ open: []; download: []; locate: [payload: { seconds: number; timestamp: string }] }>()
const source = inject(ATTACHMENT_SOURCE, NO_ATTACHMENT_SOURCE)
const name = computed(() => props.path.split('/').pop() ?? props.path)
const url = ref('')
const videoRef = ref<HTMLVideoElement | null>(null)
// width / height of the recording, once the player has read it. Until then the
// box is 16:9; after it, a portrait phone recording gets a tall box instead of
// being letterboxed into a wide one.
const ratio = ref<number | null>(null)
const loading = ref(false)
const failure = ref<'load' | 'play' | null>(null)
let generation = 0

function reset() {
  generation += 1
  url.value = ''
  ratio.value = null
  loading.value = false
  failure.value = null
}

// Fetch only on request. The player streams from the URL it is given, so a long
// recording starts without being downloaded whole first.
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
    if (mine === generation) url.value = next
  } catch {
    if (mine === generation) failure.value = 'load'
  } finally {
    if (mine === generation) loading.value = false
  }
}

function measure(event: Event) {
  const video = event.target as HTMLVideoElement
  if (video.videoWidth > 0 && video.videoHeight > 0) ratio.value = video.videoWidth / video.videoHeight
}

// 只有播放器在时才有「此刻」：视频还没打开（或打不开）时不该交出一个谁也没选过的 00:00。
function locateCurrentTime() {
  if (!videoRef.value) return
  const raw = videoRef.value.currentTime
  const seconds = Number.isFinite(raw) && raw > 0 ? Math.floor(raw) : 0
  const mins = String(Math.floor(seconds / 60)).padStart(2, '0')
  const secs = String(seconds % 60).padStart(2, '0')
  emit('locate', { seconds, timestamp: `${mins}:${secs}` })
}

// 入口灰着时为什么灰：还没打开，或者打开了但放不了（那时下面那行已经说了怎么办）。
const locateHint = computed(() =>
  !url.value && !failure.value
    ? t('work.room.video.locateNeedsPlayer')
    : failure.value
      ? failure.value === 'load'
        ? t('work.room.video.loadFailed')
        : t('work.room.video.playFailed')
      : undefined
)

watch(() => [props.topicId, props.path], reset)
onBeforeUnmount(reset)
</script>

<template>
  <div class="attachment-video">
    <!-- eslint-disable-next-line vuejs-accessibility/media-has-caption -- user-uploaded videos have no supplied caption track; keep native controls and download available -->
    <video
      v-if="url && !failure"
      ref="videoRef"
      :src="url"
      controls
      playsinline
      preload="metadata"
      :style="ratio ? { '--video-ratio': ratio } : undefined"
      :aria-label="t('work.room.video.label', { name })"
      @loadedmetadata="measure"
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
    <div class="attachment-video__actions">
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
      <BaseButton
        v-if="canOpen"
        kind="ghost"
        size="sm"
        prepend-icon="mdi-dock-right"
        class="attachment-video__open"
        :title="t('work.room.message.openFile', { name })"
        :aria-label="t('work.room.message.openFile', { name })"
        @click="emit('open')"
      >
        {{ t('work.room.file.open') }}
      </BaseButton>
      <!-- 灰着的按钮收不到悬停，为什么灰写在包着它的这一层上。 -->
      <span v-if="canLocate" :title="locateHint">
        <BaseButton
          kind="ghost"
          size="sm"
          prepend-icon="mdi-map-marker-plus-outline"
          class="attachment-video__locate"
          data-testid="video-locate"
          :disabled="!url || !!failure"
          :title="locateHint ? undefined : t('work.room.video.locateTime')"
          :aria-label="t('work.room.video.locateTime')"
          @click="locateCurrentTime"
        >
          {{ t('work.room.video.locateTime') }}
        </BaseButton>
      </span>
    </div>
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
/* The recording's own shape, no taller than --video-max-height: a portrait
   phone recording narrows instead of shrinking into a wide box. */
.attachment-video video {
  --video-max-height: 480px;
  width: min(100%, calc(var(--video-max-height) * var(--video-ratio, 16 / 9)));
  aspect-ratio: var(--video-ratio, 16 / 9);
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
.attachment-video__actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  margin-top: 4px;
}
.attachment-video__download {
  max-width: 100%;
}
</style>
