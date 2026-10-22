<script setup lang="ts">
// 详情页里这条反馈带的文件：图片直接画出来，别的给一行下载。
//
// **这一格只画，不取数。** 附件的字节要经过一次请求才拿得到（那条端点按反馈的可见性
// 判人，而 `<img src>` 带不上 Authorization），但那次取数**归页面那一层** —— 这里收
// props 出事件，所以它能在组件预览站里单独渲染。判据见 `.claude/rules/architecture.md`
// （组件不取数）与 `docs/manual/dev/scenes.md`，取数在
// `composables/useFeedbackAttachmentImages.ts`。
//
// `urls` 里**只有图片**那一档的 object URL（页面已经取好了）：8MB 的日志没人想在看页面
// 时先被下载一遍，而一张截图正是他点开这条反馈要看的东西。没有地址的按「读不到」画 ——
// 名字和大小还在，人知道少了什么，也不该因为一张图 404 就说整条反馈坏了。
import type { FeedbackAttachment } from '@/cx_types'

import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'
import ImageLightbox from '@/components/ImageLightbox.vue'
import { fmtFileSize } from '@/lib/usageFormat'

defineProps<{
  attachments: FeedbackAttachment[]
  /** 图片那一档的 object URL，按附件 id。别的类型不在里面。 */
  urls: Record<string, string>
  /** 正在下载的那一份的 id（图片本身不用它，点图是放大看）。 */
  downloading: string | null
  /** 放大看的是哪一份；`null` 表示没开着。由页面持有 —— 关掉预览之前不能把那个
   *  object URL 还回去，而地址也由页面持有。 */
  viewing: FeedbackAttachment | null
}>()

const emit = defineEmits<{
  download: [attachment: FeedbackAttachment]
  view: [attachment: FeedbackAttachment | null]
}>()

const { t } = useI18n()

function isImage(attachment: FeedbackAttachment): boolean {
  return attachment.content_type.startsWith('image/')
}
</script>

<template>
  <ul v-if="attachments.length" class="fal" data-testid="feedback-attachments">
    <li v-for="attachment in attachments" :key="attachment.id" class="fal__item">
      <!-- 图片就地画出来。等地址的时候留一块同尺寸的方块（不是转圈）：一屏几张图一起
           转圈比空白还吵。 -->
      <button
        v-if="isImage(attachment)"
        type="button"
        class="fal__thumb"
        :aria-label="t('feedback.detail.attachments.open', { name: attachment.name })"
        @click="urls[attachment.id] && emit('view', attachment)"
      >
        <img v-if="urls[attachment.id]" :src="urls[attachment.id]" :alt="attachment.name" />
        <v-icon v-else size="20" class="fal__thumb-icon" aria-hidden="true">mdi-image-outline</v-icon>
      </button>
      <v-icon v-else size="18" class="fal__icon" aria-hidden="true">mdi-file-outline</v-icon>

      <span class="fal__name">{{ attachment.name }}</span>
      <span class="fal__size t-num">{{ fmtFileSize(attachment.bytes) }}</span>
      <BaseButton kind="ghost" size="sm" :loading="downloading === attachment.id" @click="emit('download', attachment)">
        {{ t('feedback.detail.attachments.download') }}
      </BaseButton>
    </li>
  </ul>

  <ImageLightbox
    v-if="viewing && urls[viewing.id]"
    :src="urls[viewing.id]"
    :alt="viewing.name"
    @close="emit('view', null)"
  />
</template>

<style scoped>
.fal {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.fal__item {
  display: flex;
  gap: 10px;
  align-items: center;
  min-height: 44px;
  padding: 4px 4px 4px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}
/* 缩略图那一格：固定 36px，图片按 cover 缩进去 —— 一张手机截图和一张桌面截图在
   这一列里占一样大，行高不会跟着图片的长宽比跳。 */
.fal__thumb {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  padding: 0;
  overflow: hidden;
  background: var(--fill);
  border: 0;
  border-radius: var(--radius-sm);
  cursor: zoom-in;
}
.fal__thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.fal__thumb-icon,
.fal__icon {
  flex: none;
  color: var(--muted);
}
.fal__name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  color: var(--text);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.fal__size {
  flex: none;
  color: var(--muted);
  font-size: 12px;
}
</style>
