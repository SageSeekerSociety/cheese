<template>
  <!-- Version history: newest first; each version says when, who, what changed and the files it held. Data comes from `Versions.vue`. -->
  <div class="tv">
    <div v-if="loading && !versions.length" class="py-8 text-center" role="status">
      <v-progress-circular indeterminate size="28" color="primary" />
    </div>
    <BaseLoadError
      v-else-if="failed"
      :title="t('tasks.versions.loadError')"
      :error="failureReason"
      @retry="emit('retry')"
    />
    <p v-else-if="!versions.length" class="t-body c-muted">{{ t('tasks.versions.empty') }}</p>
    <template v-else>
      <p v-if="lockedNote" class="tv__locked t-body c-muted" data-testid="versions-locked">
        {{ t('tasks.attachments.locked') }}
      </p>
      <ol class="tv__list">
        <li
          v-for="(version, index) in versions"
          :key="version.number"
          :ref="(el) => remember(version.number, el)"
          class="tv__item"
          :class="{ 'tv__item--focus': version.number === focus }"
          :aria-current="version.number === focus ? 'true' : undefined"
          data-testid="task-version"
        >
          <div class="tv__head">
            <h2 class="tv__title t-title">{{ t('tasks.versions.numbered', { n: version.number }) }}</h2>
            <span v-if="index === 0" class="tv__now t-meta-read">{{ t('tasks.versions.current') }}</span>
          </div>
          <p class="tv__meta t-meta-read">
            <span>{{ when(version.at) }}</span>
            <span v-if="version.author" data-user-content>{{
              version.author.nickname || version.author.username
            }}</span>
          </p>
          <p class="tv__what t-body">{{ revisionSummary(version.changes) }}</p>
          <TaskAttachmentList
            v-if="version.files?.length"
            :attachments="version.files"
            :can-download="canDownload"
            :downloading="downloading?.version === version.number ? downloading.name : null"
            previewable
            locked-said-above
            @download="emit('download', version.number, $event)"
            @open="emit('open', version.number, $event)"
          />
          <p v-else-if="version.files === null" class="t-meta-read">{{ t('tasks.versions.filesUnknown') }}</p>
        </li>
      </ol>
    </template>
  </div>
</template>

<script setup lang="ts">
import type { ComponentPublicInstance } from 'vue'
import type { TaskVersionData, TaskVersionFileData } from '@/types/taskFiles'

import { computed, nextTick, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import dayjs from 'dayjs'

import BaseLoadError from '@/components/base/BaseLoadError.vue'
import TaskAttachmentList from '@/components/tasks/TaskAttachmentList.vue'
import { revisionSummary } from '@/lib/taskRevision'

const props = withDefaults(
  defineProps<{
    /** 新的在前。 */
    versions: TaskVersionData[]
    canDownload: boolean
    loading?: boolean
    failed?: boolean
    failureReason?: string | null
    /** 要落到的那一版（地址上的 `?version=N`）：标出来，滚到它。 */
    focus?: number | null
    /** 正在下载的那一份。 */
    downloading?: { version: number; name: string } | null
  }>(),
  { loading: false, failed: false, failureReason: null, focus: null, downloading: null }
)

const emit = defineEmits<{
  download: [version: number, file: TaskVersionFileData]
  /** 点文件名打开预览（能下载的人才点得开）。 */
  open: [version: number, file: TaskVersionFileData]
  retry: []
}>()

const { t } = useI18n()

/** 不能下载的人：「开始做之后才能下载」在页签上方说一次，不在每一行重复。 */
const lockedNote = computed(() => !props.canDownload && props.versions.some((v) => v.files?.length))

function when(at: number): string {
  return dayjs(at).format(t('tasks.roster.dateTimeFormat'))
}

const items = new Map<number, Element>()
function remember(number: number, el: Element | ComponentPublicInstance | null) {
  if (el instanceof Element) items.set(number, el)
  else items.delete(number)
}

// 带着 `?version=N` 进来：那一版读到了就滚过去。
watch(
  () => [props.focus, props.versions.length] as const,
  async ([focus]) => {
    if (focus === null) return
    await nextTick()
    items.get(focus)?.scrollIntoView?.({ block: 'start' })
  },
  { immediate: true }
)
</script>

<style scoped>
.tv {
  max-width: 680px;
}

.tv__locked {
  margin: 0 0 12px;
}

.tv__list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.tv__item {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 14px 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  scroll-margin-top: 16px;
}

.tv__item--focus {
  border-color: var(--accent);
  background: var(--accent-wash);
}

.tv__head {
  display: flex;
  gap: 8px;
  align-items: baseline;
}

.tv__title {
  margin: 0;
}

.tv__now {
  color: var(--muted);
}

.tv__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 0;
  color: var(--muted);
}

.tv__what {
  margin: 0 0 4px;
  color: var(--text);
}
</style>
