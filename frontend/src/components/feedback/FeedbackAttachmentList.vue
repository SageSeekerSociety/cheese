<script setup lang="ts">
// 详情页里这条反馈带的文件：图片直接画出来，别的给一行下载。
//
// **这一屏上的字节要经过一次请求才拿得到**：附件的端点是按反馈的可见性判人的，
// `<img src>` 发请求时带不上 Authorization（也读不到 localStorage），直接挂地址拿到
// 的是 401 加一张裂图。所以先 fetch、转成 object URL 再交给 `<img>` —— 和消息里那张
// 图（`AttachmentImage`）同一件事，只是那边走的是话题工作区那条约。
//
// object URL 不会自己消失：它把字节钉在内存里，直到被 revoke。一页可能有几张截图，
// 所以卸载时（以及列表变化时）把取出来的都还回去。
//
// 点图放大看交给 `ImageLightbox`（那一层也是只在当前文档里有效，所以不开新标签页）。
import type { FeedbackAttachment } from '@/cx_types'

import { onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { fmtBytes } from '@/composables/useFeedbackAttachments'

import { downloadFeedbackAttachment, feedbackAttachmentBlobUrl } from '@/api/feedback'
import BaseButton from '@/components/base/BaseButton.vue'
import ImageLightbox from '@/components/ImageLightbox.vue'

const props = defineProps<{ attachments: FeedbackAttachment[] }>()

const { t } = useI18n()

/** 图片那一档的 object URL，按附件 id。**只有图片预取**：8MB 的日志没人想在看页面
 *  时先被下载一遍，而一张截图正是他点开这条反馈要看的东西。 */
const urls = ref<Record<string, string>>({})
const viewing = ref<FeedbackAttachment | null>(null)
const downloading = ref<string | null>(null)

function isImage(attachment: FeedbackAttachment): boolean {
  return attachment.content_type.startsWith('image/')
}

function release(): void {
  for (const url of Object.values(urls.value)) URL.revokeObjectURL(url)
  urls.value = {}
}

/** 第几轮在取。列表换了（从一条反馈切到另一条）时，上一轮还在飞的请求回来不能把
 *  `urls` 覆盖掉 —— 覆盖了，刚取到的这一批对象地址就再也没人 revoke。 */
let generation = 0

async function load(): Promise<void> {
  const mine = ++generation
  release()
  const images = props.attachments.filter(isImage)
  const loaded: Record<string, string> = {}
  await Promise.all(
    images.map(async (attachment) => {
      try {
        loaded[attachment.id] = await feedbackAttachmentBlobUrl(attachment.id)
      } catch {
        // 取不到就按「读不到」画（名字和大小还在，人知道少了什么）。**不弹全局错误**：
        // 一页里一张图 404 不该让整条反馈看起来坏了。
      }
    })
  )
  if (mine !== generation) {
    for (const url of Object.values(loaded)) URL.revokeObjectURL(url)
    return
  }
  urls.value = loaded
}

watch(
  () => props.attachments,
  () => void load(),
  { immediate: true, deep: false }
)
onBeforeUnmount(() => {
  release()
})

/** 放大看之前先把预览收起来再还地址 —— 否则那一层手里还举着一个已经 revoke 的地址。 */
function closeLightbox(): void {
  viewing.value = null
}

async function download(attachment: FeedbackAttachment): Promise<void> {
  if (downloading.value) return
  downloading.value = attachment.id
  try {
    await downloadFeedbackAttachment(attachment)
  } finally {
    downloading.value = null
  }
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
        @click="urls[attachment.id] && (viewing = attachment)"
      >
        <img v-if="urls[attachment.id]" :src="urls[attachment.id]" :alt="attachment.name" />
        <v-icon v-else size="20" class="fal__thumb-icon" aria-hidden="true">mdi-image-outline</v-icon>
      </button>
      <v-icon v-else size="18" class="fal__icon" aria-hidden="true">mdi-file-outline</v-icon>

      <span class="fal__name">{{ attachment.name }}</span>
      <span class="fal__size t-num">{{ fmtBytes(attachment.bytes) }}</span>
      <BaseButton kind="ghost" size="sm" :loading="downloading === attachment.id" @click="download(attachment)">
        {{ t('feedback.detail.attachments.download') }}
      </BaseButton>
    </li>
  </ul>

  <ImageLightbox
    v-if="viewing && urls[viewing.id]"
    :src="urls[viewing.id]"
    :alt="viewing.name"
    @close="closeLightbox"
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
/* 缩略图那一格：固定 36px，图片按 contain 缩进去 —— 一张手机截图和一张桌面截图在
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
