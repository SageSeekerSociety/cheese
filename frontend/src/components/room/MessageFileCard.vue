<script setup lang="ts">
// 对话里一份能翻页的文档（PDF、Word、幻灯片、表格）：画成一张带封面的卡片。
//
// 只有一个文件名时，读者得点开才知道是不是要的那一份；封面（第一页）一眼就答了，
// 和微信、飞书里发一份文件看到的一样。点卡片在右侧打开那一份，走的是 <&path> 芯片
// 同一条线。
//
// 封面等卡片滚进视野才画：一段长对话里几十份文件，没人往上翻的那些不该一份份去转
// 格式（Word、幻灯片要平台转一次，约 2.5 秒，按内容哈希缓存）。画不出来时方格里摆
// 类型图标，见 AttachmentDocThumb。
import { ref } from 'vue'
import { useIntersectionObserver } from '@vueuse/core'

import AttachmentDocThumb from '../AttachmentDocThumb.vue'

import { t } from '@/i18n'

defineProps<{
  topicId: string | null
  path: string
  name: string
  /** 「文档」「表格」这样的类型说法，和原来那颗小卡片同一个来源。 */
  kind: string
}>()
const emit = defineEmits<{ open: [] }>()

const card = ref<HTMLElement | null>(null)
const seen = ref(false)
const { stop } = useIntersectionObserver(
  card,
  ([entry]) => {
    if (!entry?.isIntersecting) return
    seen.value = true
    stop()
  },
  { rootMargin: '200px' }
)
</script>

<template>
  <button
    ref="card"
    type="button"
    class="im-doc"
    data-testid="message-file-card"
    :title="t('work.room.message.openFile', { name })"
    @click="emit('open')"
  >
    <span class="im-doc__cover">
      <AttachmentDocThumb v-if="seen" :topic-id="topicId" :path="path" :icon-size="28" />
    </span>
    <span class="im-doc__text">
      <span class="im-doc__name">{{ name }}</span>
      <span class="im-doc__kind t-meta">{{ kind }}</span>
      <span class="im-doc__open">
        <v-icon size="14">mdi-dock-right</v-icon>
        {{ t('work.room.message.openInPanel') }}
      </span>
    </span>
  </button>
</template>

<style scoped>
.im-doc {
  display: flex;
  align-items: stretch;
  gap: 12px;
  width: 320px;
  max-width: 100%;
  padding: 10px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  background: var(--surface);
  text-align: left;
  cursor: pointer;
  transition:
    background-color var(--dur-quick) var(--ease-standard),
    border-color var(--dur-quick) var(--ease-standard);
}
.im-doc:hover {
  background: var(--fill);
  border-color: var(--faint);
}
.im-doc:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}
/* 封面是竖着的一页纸：方格（.att-face，40×40）在这里放大成 72×96，第一页按长边铺满。 */
.im-doc__cover {
  flex: none;
  width: 72px;
  height: 96px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--fill);
  overflow: hidden;
}
.im-doc__cover :deep(.att-face) {
  width: 100%;
  height: 100%;
  border-radius: 0;
}
.im-doc__text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  padding-top: 2px;
}
.im-doc__name {
  font-size: 14px;
  line-height: var(--lh-14);
  font-weight: 600;
  color: var(--ink);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  overflow-wrap: anywhere;
}
.im-doc__kind {
  /* 文件类型是读者要看的字：--faint 在浅色下够不到 4.5:1，用 --muted。 */
  color: var(--muted);
}
.im-doc__open {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-top: auto;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--accent-ink);
}
</style>
