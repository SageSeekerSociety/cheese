<script setup lang="ts">
// 一条支线挂着的那条主线消息，画在这段对话最上面：支线的右半边，和支线转成的任务的
// 任务页，读的是同一段对话，开头是同一条消息。
//
// 长了也收起来：它长起来，后面的回复就被推到屏幕外。判据、量高和那颗「展开 / 收起」
// 都是频道消息行用的同一份（composables/useMessageFold）。它是已经落库的消息，一直可以收。
import type { Block } from '@/cx_types'

import { computed, ref } from 'vue'

import { useMessageFold } from '@/composables/useMessageFold'

import AttachmentImage from '@/components/AttachmentImage.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import CheeseAvatar from '@/components/CheeseAvatar.vue'
import MarkdownView from '@/components/common/MarkdownView.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import { t } from '@/i18n'
import { isAgentBlock } from '@/lib/authorship'
import { artifactName, isImageBlock } from '@/lib/blockDisplay'
import { clockTime } from '@/lib/dateText'

const props = defineProps<{
  root: Block
  name: string
  /** 这个人挑过的头像；没挑过是空串，画彩色首字母。 */
  avatar: string
  refs: { mentionNames: Record<string, string>; topicTitles: Record<string, string> }
  /** 和这条消息一起发出去的附件：图片画成图，别的文件是一颗能下载的条。 */
  files?: Block[]
  /** 附件字节从哪个频道取。下载本身交给拿着这一栏的那一层，组件不自己去取。 */
  topicId?: string
}>()

const emit = defineEmits<{ (e: 'download', block: Block): void }>()

const foldEl = ref<HTMLElement | null>(null)
const foldToggleEl = ref<HTMLElement | null>(null)
const { foldable, expanded, clamped, foldPx, toggle } = useMessageFold(
  foldEl,
  // 只收正文：一张图本身就是这条消息，没有「展开」可言。
  computed(() => props.root.kind === 'message'),
  computed(() => props.root.content),
  // 展开后那颗「收起」在正文末尾，正文高过一屏就掉到视野外：交给它自己把按钮带回来。
  foldToggleEl
)
const time = computed(() => clockTime(new Date(props.root.created_at).getTime()))

function download(block: Block) {
  emit('download', block)
}
</script>

<template>
  <article class="thread-root">
    <!-- 队友一律 CheeseAvatar（和消息行同一套标记）；人走 UserAvatar：挑过头像的显示
         头像，没挑过就按 handle 取色画首字母（颜色跟 handle，不跟昵称）。 -->
    <CheeseAvatar v-if="isAgentBlock(root)" class="thread-root__avatar" :size="28" :name="name" :handle="root.author" />
    <UserAvatar v-else class="thread-root__avatar" :size="28" :name="name" :avatar="avatar" :seed="root.author" />
    <div class="thread-root__body">
      <div class="thread-root__head">
        <span class="thread-root__name">{{ name }}</span>
        <span class="t-meta thread-root__time">{{ time }}</span>
      </div>
      <!-- 只发了图、没有字的时候，这条消息本身就是那张图。 -->
      <div v-if="root.kind === 'attachment'" class="thread-root__files">
        <AttachmentImage
          v-if="isImageBlock(root)"
          :topic-id="topicId ?? null"
          :path="root.content"
          :alt="artifactName(root)"
        />
        <BaseButton
          v-else
          kind="ghost"
          prepend-icon="mdi-file-document-outline"
          append-icon="mdi-download-outline"
          class="thread-root__file"
          :title="t('work.room.message.downloadFile', { name: artifactName(root) })"
          @click="download(root)"
        >
          <span class="text-truncate">{{ artifactName(root) }}</span>
        </BaseButton>
      </div>
      <template v-else>
        <div
          ref="foldEl"
          class="fold"
          :class="{ 'fold-clamped': clamped }"
          :style="clamped ? { maxHeight: `${foldPx}px` } : undefined"
        >
          <MarkdownView class="thread-root__text" :source="root.content" as="chat" :names="refs" />
          <div v-if="clamped" class="fold-fade" aria-hidden="true" />
        </div>
        <button
          v-if="foldable"
          ref="foldToggleEl"
          type="button"
          class="fold-toggle"
          :aria-expanded="expanded"
          data-testid="thread-root-fold"
          @click="toggle"
        >
          <v-icon size="16">{{ expanded ? 'mdi-chevron-up' : 'mdi-chevron-down' }}</v-icon>
          {{ expanded ? t('work.room.message.showLess') : t('work.room.message.showMore') }}
        </button>
        <!-- 和这条消息一起发出去的文件，按主线的样子跟在正文后面：图是图，别的
             文件是一颗能下载的条。 -->
        <div v-if="files?.length" class="thread-root__files">
          <template v-for="file in files" :key="file.id">
            <AttachmentImage
              v-if="isImageBlock(file)"
              :topic-id="topicId ?? null"
              :path="file.content"
              :alt="artifactName(file)"
            />
            <BaseButton
              v-else
              kind="ghost"
              prepend-icon="mdi-file-document-outline"
              append-icon="mdi-download-outline"
              class="thread-root__file"
              :title="t('work.room.message.downloadFile', { name: artifactName(file) })"
              @click="download(file)"
            >
              <span class="text-truncate">{{ artifactName(file) }}</span>
            </BaseButton>
          </template>
        </div>
      </template>
    </div>
  </article>
</template>

<style scoped src="@/styles/message-fold.css"></style>

<style scoped>
.thread-root {
  display: flex;
  gap: 10px;
  padding: 12px 16px 4px;
}
.thread-root__avatar {
  flex: none;
}
.thread-root__body {
  min-width: 0;
  flex: 1;
}
.thread-root__head {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.thread-root__name {
  font-size: 14px;
  line-height: var(--lh-14);
  font-weight: 600;
  color: var(--ink);
}
.thread-root__time {
  color: var(--faint);
}
.thread-root__text {
  font-size: 15px;
  line-height: var(--lh-15);
  color: var(--text);
  overflow-wrap: anywhere;
}
.thread-root__files {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
  margin-top: 6px;
}
.thread-root__file {
  max-width: 100%;
}
</style>
