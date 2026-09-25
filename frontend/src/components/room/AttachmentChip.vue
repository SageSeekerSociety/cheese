<script setup lang="ts">
// 输入框里待发的一个附件。
//
// 左边那个记号说它是什么（图片的缩略图、文档的首页、其余类型的图标、还在上传时
// 的转圈），右边写着文件名：名字必须一直在，否则上传中那一格只是一个转圈，说不
// 出是哪个文件。四种状态占同一个记号格，上传完成那一刻标签不跳。
import type { PendingAttachment } from '../../lib/attachments'

import { computed } from 'vue'

import { fileIcon, hasPagePreview } from '../../lib/fileKind'
import AttachmentDocThumb from '../AttachmentDocThumb.vue'
import AttachmentImage from '../AttachmentImage.vue'

import ComposerChip from './ComposerChip.vue'

import { t } from '@/i18n'

const props = defineProps<{
  topicId: string | null
  attachment: PendingAttachment
}>()
const emit = defineEmits<{ (e: 'remove'): void }>()

// 上传中那一格还没有工作区路径，名字只有它自己记着的那一份。
const name = computed(() => props.attachment.name ?? props.attachment.path.split('/').pop() ?? '')
const isImage = computed(() => props.attachment.mime.startsWith('image/'))
// 按后缀问，不按 mime：平台那个转换服务也是照后缀决定转不转的，两边用同一个判据。
const isDocument = computed(() => hasPagePreview(name.value))
</script>

<template>
  <ComposerChip
    :label="name"
    :remove-label="t('work.room.composer.removeAttachment', { name })"
    @remove="emit('remove')"
  >
    <template #face>
      <!-- 图片和文档那两个组件的根元素自己就是记号格（它们要在加载中、成功、
           失败三种状态下都占住它）；剩下两种状态没有组件，记号格由这里画。
           传完的那一刻转圈原地换成缩略图或图标：先淡出，新的再淡入。 -->
      <Transition name="face" mode="out-in">
        <AttachmentImage v-if="!attachment.uploading && isImage" thumb :topic-id="topicId" :path="attachment.path" />
        <AttachmentDocThumb
          v-else-if="!attachment.uploading && isDocument"
          :topic-id="topicId"
          :path="attachment.path"
          :icon-size="14"
        />
        <span v-else :key="attachment.uploading ? 'uploading' : 'done'" class="att-face">
          <v-progress-circular v-if="attachment.uploading" indeterminate size="12" width="1.5" />
          <v-icon v-else size="14">{{ fileIcon(name) }}</v-icon>
        </span>
      </Transition>
    </template>
  </ComposerChip>
</template>

<style scoped>
.face-enter-active {
  transition:
    opacity var(--dur-base) var(--ease-out),
    transform var(--dur-base) var(--ease-out);
}
.face-leave-active {
  transition: opacity var(--dur-quick) var(--ease-in);
}
.face-enter-from {
  opacity: 0;
  transform: scale(0.8);
}
.face-leave-to {
  opacity: 0;
}
</style>
