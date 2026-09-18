<script setup lang="ts">
// 输入栏里待发的一个附件。
//
// 每一格都是同一个块，因为一条消息里常常同时有图片和文档——两种形状并排读起来
// 是两样不相干的东西，而且上传完成的那一刻形状一换，整条会跳。块的左边那个方格
// 说明它是什么（图片的缩略图、文档的首页、其余类型的图标、还在上传时的转圈），
// 右边写着文件名：名字必须一直在，否则上传中那一格只是一个转圈，说不出是哪个
// 文件。
import type { PendingAttachment } from '../lib/attachments'

import { computed } from 'vue'

import { fileIcon, hasPagePreview } from '../lib/fileKind'

import AttachmentDocThumb from './AttachmentDocThumb.vue'
import AttachmentImage from './AttachmentImage.vue'

import { t } from '@/i18n'

const props = defineProps<{
  topicId: string | null
  attachment: PendingAttachment
}>()
const emit = defineEmits<{ (e: 'remove'): void }>()

// 上传中那一格还没有工作区路径，名字只有它自己记着的那一份。
const name = computed(
  () => props.attachment.name ?? props.attachment.path.split('/').pop() ?? t('workspace.attachment.unnamed')
)
const isImage = computed(() => props.attachment.mime.startsWith('image/'))
// 按后缀问，不按 mime：平台那个转换服务也是照后缀决定转不转的，两边用同一个判据。
const isDocument = computed(() => hasPagePreview(name.value))
</script>

<template>
  <div class="att-card">
    <!-- 一张卡里只有一个方格。图片和文档那两个组件的根元素自己就是这个方格
         （它们要在加载中、成功、失败三种状态下都占住它），所以这里不再包一层；
         剩下两种状态没有组件，方格由这里画。 -->
    <AttachmentImage v-if="!attachment.uploading && isImage" thumb :topic-id="topicId" :path="attachment.path" />
    <AttachmentDocThumb v-else-if="!attachment.uploading && isDocument" :topic-id="topicId" :path="attachment.path" />
    <span v-else class="att-face">
      <v-progress-circular v-if="attachment.uploading" indeterminate size="18" width="2" />
      <v-icon v-else size="22">{{ fileIcon(name) }}</v-icon>
    </span>
    <span class="att-card__name t-meta c-text">{{ name }}</span>
    <!-- 名字在块边缘就截断了，全名得有地方看。不用 title 属性：系统原生气泡要
         鼠标停住约一秒才弹，弹出来又是屏幕上唯一不跟随主题的东西，读者多半会
         以为没有。 -->
    <v-tooltip activator="parent" location="top" :text="name" />
    <!-- × 自己说得清，标签只给读屏软件；给它 title 的话，这里会同时冒出两个气泡。 -->
    <button
      type="button"
      class="att-card__remove"
      :aria-label="t('workspace.attachment.remove')"
      @click="emit('remove')"
    >
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
