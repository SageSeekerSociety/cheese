<script setup lang="ts">
// 输入栏里待发的一个附件。
//
// 每一格都是同一个块，因为一条消息里常常同时有图片和文档——两种形状并排读起来
// 是两样不相干的东西，而且上传完成的那一刻形状一换，整条会跳。块的左边那个方格
// 说明它是什么（图片的缩略图、PDF 的首页、其余类型的图标、还在上传时的转圈），
// 右边写着文件名：名字必须一直在，否则上传中那一格只是一个转圈，说不出是哪个
// 文件。
import type { PendingAttachment } from '../lib/attachments'

import { computed } from 'vue'

import { fileIcon } from '../lib/fileKind'

import AttachmentImage from './AttachmentImage.vue'
import AttachmentPdfThumb from './AttachmentPdfThumb.vue'

const props = defineProps<{
  topicId: string | null
  attachment: PendingAttachment
}>()
const emit = defineEmits<{ (e: 'remove'): void }>()

// 上传中那一格还没有工作区路径，名字只有它自己记着的那一份。
const name = computed(() => props.attachment.name ?? props.attachment.path.split('/').pop() ?? '附件')
const isImage = computed(() => props.attachment.mime.startsWith('image/'))
const isPdf = computed(() => props.attachment.mime === 'application/pdf')
</script>

<template>
  <div class="att-card" :title="name">
    <!-- 一张卡里只有一个方格。图片和 PDF 那两个组件的根元素自己就是这个方格
         （它们要在加载中、成功、失败三种状态下都占住它），所以这里不再包一层；
         剩下两种状态没有组件，方格由这里画。 -->
    <AttachmentImage v-if="!attachment.uploading && isImage" thumb :topic-id="topicId" :path="attachment.path" />
    <AttachmentPdfThumb v-else-if="!attachment.uploading && isPdf" :topic-id="topicId" :path="attachment.path" />
    <span v-else class="att-face">
      <v-progress-circular v-if="attachment.uploading" indeterminate size="18" width="2" />
      <v-icon v-else size="22">{{ fileIcon(name) }}</v-icon>
    </span>
    <span class="att-card__name t-meta c-text">{{ name }}</span>
    <button type="button" class="att-card__remove" title="移除" @click="emit('remove')">
      <v-icon size="12">mdi-close</v-icon>
    </button>
  </div>
</template>

<style scoped>
.att-card {
  position: relative;
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: 8px;
  width: 180px;
  max-width: 100%;
  height: 56px;
  padding: 0 8px;
  border-radius: var(--radius-md);
  border: 1px solid var(--line);
  background: var(--surface);
}
/* 名字占满剩下的宽度并在末尾截断。min-width: 0 是 flex 子项能被压缩的前提——
   少了它，长文件名会把块顶宽，一排块就不再一样大。 */
.att-card__name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.att-card__remove {
  display: inline-flex;
  position: absolute;
  top: -6px;
  right: -6px;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: var(--radius-pill);
  border: 1px solid var(--line);
  background: var(--surface);
  color: var(--muted);
  line-height: 1;
  cursor: pointer;
}
.att-card__remove:hover {
  color: var(--ink);
}
</style>
