<script setup lang="ts">
/**
 * 没问到看的人的那些题，在频道里占的那一行。
 *
 * 一张提问卡占满消息那一栏：题目、几个选项、末行的输入框。问到你自己的时候那是对的
 * ——你正要作答。没问到你的那些，你顶多想知道「芝士在等谁、等什么」，一整张卡就太占
 * 地方了。这一行只说这么多：谁问、问谁、题目一句话、已经怎么样了。
 *
 * 它自己不知道点开以后长什么样：单题（`AskQuickReplies`）和一次问的几道题
 * （`AskGroupCard`）各画各的卡，这一行只负责收着和那颗箭头。
 *
 * 什么算「问别人」不在这里判（`lib/askGroups` 的 `askAsksViewer`）：组件只画。
 */
import type { Block } from '../../cx_types'
import type { RefNames } from '../../lib/refChip'

import { computed } from 'vue'

import { t } from '../../i18n'
import { askMultiSelect } from '../../lib/askGroups'
import { askAnswers, replySnippet } from '../../lib/blockDisplay'

const props = defineProps<{
  /** 这一行收着的题：单题就一道，一组就整组。 */
  blocks: Block[]
  /** 问的那一位的显示名；不给就只写问谁。 */
  asker?: string
  names: Record<string, string>
  refs: RefNames
  expanded: boolean
  /** 这里已经不能回答了：任务关了、频道归档了。 */
  closed?: boolean
  viewer?: string
}>()

const emit = defineEmits<{ (e: 'toggle'): void }>()

const first = computed(() => props.blocks[0]!)
const count = computed(() => props.blocks.length)
const meta = computed(() => (first.value.meta ?? {}) as { asked?: unknown[]; asked_all?: boolean })

/** 问了谁：点名就写名字，问所有人写「所有人」，没点名写「频道里的人」。 */
const whom = computed(() => {
  if (meta.value.asked_all) return t('ask.fold.everyone')
  const asked = (meta.value.asked ?? []).filter((h): h is string => typeof h === 'string')
  if (!asked.length) return t('ask.fold.anyone')
  return asked.map((h) => props.names[h] ?? h).join(t('work.room.roster.listSeparator'))
})

/** 题目收成一行：题目就是那张卡的标题，长的截断，完整的在点开的卡里。 */
const title = computed(() => replySnippet(first.value, props.refs, 80))

/** 已经怎么样了：有人答过就写答案，没人答就写题还开着。 */
const state = computed(() => {
  const answered = props.blocks.flatMap((b) => askAnswers(b)).filter((a) => !a.skipped)
  if (answered.length) {
    const one = answered[0]!
    return t('ask.fold.answered', { name: props.names[one.by] ?? one.by, text: one.text })
  }
  if (props.closed) return t('ask.fold.closed')
  return count.value > 1 ? t('ask.fold.questions', { n: count.value }) : t('ask.fold.waiting')
})

const multi = computed(() => askMultiSelect(first.value))
</script>

<template>
  <button type="button" class="ask-fold" :aria-expanded="expanded" @click="emit('toggle')">
    <span class="ask-fold__who">
      {{ asker ? t('ask.fold.whoNamed', { asker, people: whom }) : t('ask.fold.whoBare', { people: whom }) }}
    </span>
    <!-- 点开之后题目就在下面那张卡里，这一行不再说一遍。 -->
    <span v-if="!expanded" class="ask-fold__title">{{ title }}</span>
    <span class="ask-fold__state">{{ state }}</span>
    <span v-if="multi && count === 1" class="ask-fold__multi">{{ t('ask.fold.multi') }}</span>
    <v-icon size="16" class="ask-fold__chevron">{{ expanded ? 'mdi-chevron-up' : 'mdi-chevron-down' }}</v-icon>
  </button>
</template>

<style scoped>
/* 一行：谁问谁、题目、等没等到。字和别的元信息同档（13），没有外框——它是对话里的
   一行说明，不是一张卡。名字和题目读得到，其余降一档。 */
.ask-fold {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 2px 8px;
  width: 100%;
  margin-top: 2px;
  padding: 2px 0;
  font: inherit;
  font-size: 13px;
  line-height: var(--lh-13);
  text-align: left;
  color: var(--muted);
  background: none;
  border: 0;
  cursor: pointer;
}
.ask-fold:hover .ask-fold__title {
  color: var(--ink);
}
.ask-fold:focus-visible {
  outline-offset: 2px;
}
.ask-fold__who,
.ask-fold__state,
.ask-fold__multi {
  flex: none;
}
/* 题目最多两行：窄屏上一行放不下，收起来的这一行也还是比整张卡矮得多。 */
.ask-fold__title {
  display: -webkit-box;
  min-width: 0;
  max-width: 100%;
  overflow: hidden;
  color: var(--text);
  overflow-wrap: anywhere;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  transition: color var(--dur-quick) var(--ease-standard);
}
.ask-fold__state,
.ask-fold__multi {
  color: var(--faint);
}
.ask-fold__chevron {
  flex: none;
  color: var(--faint);
  align-self: center;
}
</style>
