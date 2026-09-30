<template>
  <!-- 「说明」页签的画面：题目详情（富文本）、视频、材料。阅读宽度。取数在 `Brief.vue`。 -->
  <div v-if="taskData" class="tb">
    <section class="tb__sec">
      <h2 class="tb__h t-title">{{ t('tasks.brief.details') }}</h2>
      <TipTapViewer v-if="isTipTapJson" class="tb__md" :value="tipTapContent" />
      <div v-else-if="renderedMarkdown" class="markdown-body tb__md t-reading" v-html="renderedMarkdown" />
      <p v-else class="tb__note">{{ t('tasks.brief.empty') }}</p>
    </section>

    <!-- 视频：只嵌 B 站；别的平台给一条（过了 https 校验的）链接，不假装能放。 -->
    <section v-if="videoLink" class="tb__sec">
      <h2 class="tb__h t-title">{{ t('tasks.brief.video') }}</h2>
      <iframe
        v-if="videoEmbed"
        :src="videoEmbed"
        :title="t('tasks.brief.videoFrame')"
        frameborder="0"
        allowfullscreen
        referrerpolicy="no-referrer"
        class="tb__video"
      />
      <template v-else>
        <p class="tb__note">{{ t('tasks.brief.videoExternal') }}</p>
        <a :href="videoLink" target="_blank" rel="noopener" class="tb__link">{{ videoLink }}</a>
      </template>
    </section>

    <!-- 材料：清单与下载次数都是服务端的，点不点得动也由它说。没有材料就不画这一段。 -->
    <section v-if="attachments.length" class="tb__sec">
      <h2 class="tb__h t-title">{{ t('tasks.brief.materials') }}</h2>
      <TaskAttachmentList
        :attachments="attachments"
        :can-download="canDownload"
        :downloading-id="downloadingId"
        @download="emit('download', $event)"
      />
    </section>
  </div>
</template>

<script setup lang="ts">
import type { TaskAttachmentData } from '@/network/api/tasks/types'
import type { Task } from '@/types'

import { computed, defineAsyncComponent } from 'vue'
import { useI18n } from 'vue-i18n'

import { MarkdownRenderer } from '@/components/chat/services/markdownRenderer'
import TaskAttachmentList from '@/components/tasks/TaskAttachmentList.vue'

const props = defineProps<{
  taskData: Task | null
  attachments: TaskAttachmentData[]
  canDownload: boolean
  downloadingId: number | null
}>()

const emit = defineEmits<{ download: [file: TaskAttachmentData] }>()

const { t } = useI18n()

const TipTapViewer = defineAsyncComponent(() => import('@/components/common/Editor/TipTapViewer.vue'))
const markdownRenderer = new MarkdownRenderer()

/** 题目详情是富文本：TipTap JSON 或 Markdown，两种都认。 */
const isTipTapJson = computed(() => {
  const raw = props.taskData?.description ?? ''
  if (!raw) return false
  try {
    const parsed = JSON.parse(raw)
    return typeof parsed === 'object' && parsed !== null && parsed.type === 'doc'
  } catch {
    return false
  }
})
const tipTapContent = computed(() => {
  try {
    return JSON.parse(props.taskData?.description ?? '{}')
  } catch {
    return { type: 'doc', content: [] }
  }
})
const renderedMarkdown = computed(() => {
  const raw = props.taskData?.description ?? ''
  if (!raw || isTipTapJson.value) return ''
  return markdownRenderer.render(raw)
})

const videoLink = computed(() => {
  const url = props.taskData?.videoUrl
  if (!url) return null
  try {
    if (new URL(url).protocol === 'https:') return url
  } catch {
    // 不是个 URL
  }
  return null
})
const videoEmbed = computed(() => {
  const bvid = videoLink.value?.match(/bilibili\.com\/video\/(BV[\w]+)/)?.[1]
  return bvid ? `//player.bilibili.com/player.html?bvid=${bvid}&autoplay=0` : null
})
</script>

<style scoped>
.tb {
  max-width: 680px;
}

.tb__md :deep(h1),
.tb__md :deep(h2),
.tb__md :deep(h3),
.tb__md :deep(h4) {
  margin: 16px 0 6px;
  color: var(--ink);
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
}

.tb__md :deep(p) {
  margin: 0 0 8px;
}

.tb__md :deep(ul),
.tb__md :deep(ol) {
  margin: 0 0 8px;
  padding-left: 20px;
}

.tb__sec + .tb__sec {
  margin-top: 28px;
}

.tb__h {
  margin: 0 0 10px;
}

.tb__video {
  width: 100%;
  aspect-ratio: 16 / 9;
  border: 0;
  border-radius: var(--radius-md);
}

.tb__link {
  color: var(--accent-ink);
  font-size: 13px;
  word-break: break-all;
}

.tb__note {
  margin: 0 0 6px;
  color: var(--muted);
  font-size: 13px;
}
</style>
