<script setup lang="ts">
// 面板上人说的一句：话里 `<#id>` 引用的东西画成标签（和输入框里挑中时一个样子），下面
// 每样一行、记号打头：随这一句交的文件、选中交给芝士的那段字、芝士读的那一页。
import type { AssistantCited } from '@/stores/agentThread'

import { computed } from 'vue'

import { refChip, type RefNames, refTokens } from '@/lib/refChip'

const props = defineProps<{
  text: string
  files?: string[]
  /** 在哪一页问的（那一页的名字）。 */
  askedOn?: string
  selection?: string
  cited?: AssistantCited[]
}>()

const names = computed<RefNames>(() => ({
  mentionNames: {},
  topicTitles: {},
  cited: Object.fromEntries(
    (props.cited ?? []).map((c) => [c.id, { kind: c.kind === 'thread' ? 'channel' : c.kind, title: c.title }])
  ),
}))

function escape(text: string): string {
  return text.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`)
}

/** 话照原样，引用换成标签。标签是 `refChip` 画的元素，序列化成 HTML 放进来，和对话里一样。 */
const html = computed(() => {
  let out = ''
  let at = 0
  for (const ref of refTokens(props.text)) {
    out += escape(props.text.slice(at, ref.index))
    out += ref.kind === '#' ? refChip('#', ref.id, names.value, { focusable: false }).outerHTML : escape(ref.id)
    at = ref.index + ref.length
  }
  return out + escape(props.text.slice(at))
})
</script>

<template>
  <div class="asked">
    <!-- eslint-disable-next-line vue/no-v-html -- the person's own words, escaped above; chips are built by refChip -->
    <p v-if="text" class="asked__t" data-user-content v-html="html" />
    <p v-for="name in files ?? []" :key="name" class="asked__file">
      <v-icon size="14">mdi-paperclip</v-icon><span data-user-content>{{ name }}</span>
    </p>
    <p v-if="selection" class="asked__file">
      <v-icon size="14">mdi-format-quote-close</v-icon><span class="asked__one" data-user-content>{{ selection }}</span>
    </p>
    <p v-if="askedOn" class="asked__file">
      <v-icon size="14">mdi-eye-outline</v-icon><span data-user-content>{{ askedOn }}</span>
    </p>
  </div>
</template>

<style scoped>
.asked {
  align-self: flex-end;
  max-width: 85%;
  padding: 8px 12px;
  margin: 0;
  color: var(--ink);
  white-space: pre-wrap;
  background: var(--fill-2);
  border-radius: var(--radius-lg);
}

.asked__t {
  margin: 0;
}

.asked__file {
  display: flex;
  gap: 4px;
  align-items: center;
  margin: 4px 0 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.asked__one {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
