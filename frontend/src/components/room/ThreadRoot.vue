<script setup lang="ts">
// 一条支线挂着的那条主线消息，画在这段对话最上面：支线的右半边，和支线转成的任务的
// 任务页，读的是同一段对话，开头是同一条消息。
//
// 长了也收起来：它长起来，后面的回复就被推到屏幕外。判据、量高和那颗「展开 / 收起」
// 都是频道消息行用的同一份（composables/useMessageFold）。它是已经落库的消息，一直可以收。
import type { Block } from '@/cx_types'

import { computed, ref } from 'vue'

import { useMessageFold } from '@/composables/useMessageFold'

import CheeseAvatar from '@/components/CheeseAvatar.vue'
import MarkdownView from '@/components/common/MarkdownView.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import { t } from '@/i18n'
import { isAgentBlock } from '@/lib/authorship'
import { clockTime } from '@/lib/dateText'
import { renderPlain } from '@/lib/renderMessage'

const props = defineProps<{
  root: Block
  name: string
  /** 这个人挑过的头像；没挑过是空串，画彩色首字母。 */
  avatar: string
  refs: { mentionNames: Record<string, string>; topicTitles: Record<string, string> }
}>()

const foldEl = ref<HTMLElement | null>(null)
const foldToggleEl = ref<HTMLElement | null>(null)
const { foldable, expanded, clamped, foldPx, toggle } = useMessageFold(
  foldEl,
  computed(() => true),
  computed(() => props.root.content),
  // 展开后那颗「收起」在正文末尾，正文高过一屏就掉到视野外：交给它自己把按钮带回来。
  foldToggleEl
)
const time = computed(() => clockTime(new Date(props.root.created_at).getTime()))
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
      <div
        ref="foldEl"
        class="fold"
        :class="{ 'fold-clamped': clamped }"
        :style="clamped ? { maxHeight: `${foldPx}px` } : undefined"
      >
        <MarkdownView
          v-if="isAgentBlock(root)"
          class="thread-root__text"
          :source="root.content"
          as="chat"
          :names="refs"
        />
        <div v-else class="thread-root__text thread-root__text--plain" v-html="renderPlain(root.content, refs)" />
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
.thread-root__text--plain {
  white-space: pre-wrap;
}
</style>
