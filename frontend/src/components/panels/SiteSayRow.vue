<script setup lang="ts">
// 芝士说话的那一条：头像 + 名字 + markdown 正文，和对话栏一样（走同一个
// MarkdownView）。原文摆出来是一堆星号和反引号，粗体、列表、代码块全丢了信息；
// 引用 token 也照对话栏展开成 chip —— 光看 `<@handle>` `<&path>` 是认不出人的。
// chip 是 v-html 塞进来的，点击只能从容器上委派。
//
// 这一只只画：谁的名字、正文里的引用认不认人、这一条要不要夹、夹了没有，都由
// 外面给。「要不要夹」是两个答案里取一个来的（量出来的高度，或者数出来的行数），
// 那件事依赖面板宽度，归取数那一层（`useSiteClamp`）。
import type { Block } from '../../cx_types'

import { computed } from 'vue'

import { isAgentBlock } from '../../lib/authorship'
import { countLines, SITE_CLAMP_LINES } from '../../lib/siteLog'
import CheeseAvatar from '../CheeseAvatar.vue'
import MarkdownView from '../common/MarkdownView.vue'

import { t } from '@/i18n'
import { clockTime } from '@/lib/dateText'

const props = defineProps<{
  block: Block
  /** 名册里查不到名字的 AI 发言按这个名字称呼（项目 AI 队友的名字）。 */
  agentName: string
  /** 房间名册 handle → 名字。 */
  memberNames: Record<string, string>
  /** 这一条长到要夹：量出来的答案，还没有量过时用数出来的那个。 */
  long: boolean
  /** 这一条摊开了没有（按 block id 记在外面）。 */
  expanded: boolean
}>()

const emit = defineEmits<{
  (e: 'toggle', id: string): void
  (e: 'mention-click', handle: string): void
  (e: 'open-topic', id: string): void
  (e: 'open-file', path: string): void
}>()

function fmtTime(iso: string): string {
  // Local 24-hour HH:mm, not raw UTC slice, and not the browser's default locale.
  return clockTime(new Date(iso).getTime())
}

// 同对话栏：名册上没有的 AI 作者显示成「芝士」，不把 handle 摆出来。
const authorLabel = computed(
  () => props.memberNames[props.block.author] || (isAgentBlock(props.block) ? props.agentName : props.block.author)
)

// 发到对话里的那条（kind=message）走过点名，@ 到的人被通知了，画 @chip；只在现场
// 说的过程话（kind=event）没有通知谁，提到的人只是名字。
const sayRefs = computed(() => ({ mentionNames: props.memberNames, topicTitles: {} }))
const toldRefs = computed(() => ({ ...sayRefs.value, notified: true }))

// 文件在哪一份里找，由面板按它此刻读的那段对话决定。
function onSayClick(event: MouseEvent): void {
  const chip = (event.target as HTMLElement | null)?.closest('.mention, .name-ref') as HTMLElement | null
  if (!chip) return
  if (chip.dataset.handle) emit('mention-click', chip.dataset.handle)
  else if (chip.dataset.topic) emit('open-topic', chip.dataset.topic)
  else if (chip.dataset.file) emit('open-file', chip.dataset.file)
}
</script>

<template>
  <!-- The avatar letter and the name beside it come from the same source: this
       row's author. -->
  <div class="site-msg">
    <CheeseAvatar :size="26" :name="authorLabel" :handle="block.author" class="site-msg__av" />
    <div class="site-msg__main">
      <div class="site-msg__meta">
        <span class="site-msg__name">{{ authorLabel }}</span>
        <span class="t-meta">{{ fmtTime(block.created_at) }}</span>
      </div>
      <!-- Rendered as prose, not as raw text: bold, lists and code blocks look
           the same here as in the chat. Keep this explanation in step with
           MarkdownView's. -->
      <!-- Clamp = max-height + a fade, NOT -webkit-line-clamp: that one is
           ignored the moment the markdown holds a block-level <pre>, so a
           128-line code block rendered at full height and the expand button
           opened nothing. The fade sits on the wrapper, because the body's own
           overflow:hidden would clip it. -->
      <div class="site-msg__clip" :class="{ 'site-msg__clip--clamped': long && !expanded }">
        <MarkdownView
          class="site-msg__body md-content"
          :class="{ 'site-msg__body--clamped': long && !expanded }"
          :style="{ '--site-clamp-lines': SITE_CLAMP_LINES }"
          :data-site-body="block.id"
          :source="block.content"
          as="chat"
          :names="block.kind === 'message' ? toldRefs : sayRefs"
          @click="onSayClick($event)"
        />
      </div>
      <!-- Not expanded outright when long: a few thousand characters of output
           would push everything around it off the screen, and this tab is worth
           exactly what it shows at a glance. Collapse to 12 lines and let the
           reader open it. -->
      <button
        v-if="long"
        type="button"
        class="site-msg__more"
        :aria-expanded="expanded"
        @click="emit('toggle', block.id)"
      >
        {{
          expanded ? t('work.room.site.collapse') : t('work.room.site.expandAll', { count: countLines(block.content) })
        }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.site-msg {
  display: flex;
  gap: 8px;
  margin: 6px 0 2px;
}
.site-msg__av {
  flex: 0 0 auto;
  margin-top: 1px;
}
.site-msg__main {
  flex: 1 1 auto;
  min-width: 0;
}
.site-msg__meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 2px;
}
.site-msg__name {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}
/* 芝士说话的那一段：排版规则（标题、列表、代码块、表格）来自全局的 .md-content，
   这里只定这一栏自己的字号 —— 现场比对话栏密，13px 和旁边那些工具行对得上。 */
.site-msg__body {
  font-size: 13px;
  /* 行高按 token 取 13px 那一档：折叠的 max-height 正好是它的 12 倍，两处都写死
     数字的话，改一处另一处就跟着错位。 */
  line-height: var(--lh-13);
  word-break: break-word;
  color: var(--text);
}
/* 底部渐隐要盖在正文上面，所以它在正文外面那一层：放进正文里会被正文自己的
   overflow: hidden 一起剪掉。 */
.site-msg__clip {
  position: relative;
}
.site-msg__clip--clamped::after {
  position: absolute;
  right: 0;
  bottom: 0;
  left: 0;
  height: 28px;
  pointer-events: none;
  background: linear-gradient(to bottom, transparent, var(--surface));
  content: '';
}
/* Collapsed long entry: an explicit max-height, NOT -webkit-line-clamp. The
   clamp version is ignored the moment the markdown holds a block-level <pre>,
   so a 128-line code block rendered at its full height (measured 2615px) while
   the button still said 展开全部. max-height clips the real height, block
   children included. The line count comes from SITE_CLAMP_LINES via a bound
   custom property rather than a literal here: the template asks that same
   module whether to render the 展开 button, so if the two drift an entry gets
   clamped with no way out of the clamp. */
.site-msg__body--clamped {
  max-height: calc(var(--site-clamp-lines) * var(--lh-13));
  overflow: hidden;
}
.site-msg__more {
  margin-top: 4px;
  padding: 0;
  border: 0;
  background: none;
  font-size: 12px;
  color: var(--faint);
  cursor: pointer;
}
.site-msg__more:hover {
  color: var(--text);
  text-decoration: underline;
}
</style>
