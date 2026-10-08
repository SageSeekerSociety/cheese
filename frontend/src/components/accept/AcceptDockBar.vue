<script setup lang="ts">
// 贴在输入框上方的一条：这次交付现在怎样，轮到人的时候就在这一条上决定。
//
// 它原来是对话末尾一张 280px 高的卡，后来收成一行、点开再展开整张卡；可展开的那张卡
// 把交付的详情、检查、按钮全摊在对话栏里，挤得对话只剩几行。现在详情在「改动」页顶部
// （审阅本来就在那边看），这一条只说两件事：现在在等什么，以及轮到人时的「退回 /
// 采纳」。点状态那半句就去「改动」页看。
//
// 这一条只画，不知道卡里是什么：状态词、圈、按钮上的字由调用方算好递进来，点的每一下
// 报回去。
import type { BoardColumn } from '@/cx_types'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'
import { columnDotStyle } from '@/lib/board'

const props = withDefaults(
  defineProps<{
    title: string
    /** 待审阅的卡画「该谁动」的圈；历史卡和已采纳的卡画图标。 */
    column?: BoardColumn | null
    icon: string
    color: string
    /** 轮到人决定：放「退回」和「采纳」。 */
    decide?: boolean
    acceptLabel?: string
    /** 采纳为什么现在点不了（就是按钮的 title），空表示可以。 */
    blockedTitle?: string | null
    busy?: boolean
    /** 手机上对话和「改动」是两个页签：这里只放一颗「审阅」，决定在「改动」页底部。 */
    reviewButton?: boolean
    /** 已采纳的卡：给反悔留一个「撤回采纳」。 */
    revoke?: boolean
    /** 历史卡：点这一条在上面展开它当年的那张卡。 */
    expandable?: boolean
    expanded?: boolean
  }>(),
  {
    column: null,
    decide: false,
    acceptLabel: '',
    blockedTitle: null,
    busy: false,
    reviewButton: false,
    revoke: false,
    expandable: false,
    expanded: false,
  }
)

const emit = defineEmits<{
  (e: 'review'): void
  (e: 'toggle'): void
  (e: 'accept'): void
  (e: 'reject'): void
  (e: 'revoke'): void
}>()

function onStatus() {
  if (props.expandable) emit('toggle')
  else emit('review')
}
</script>

<template>
  <div class="accept-bar">
    <!-- 状态那半句：待审阅的卡点它去「改动」页，历史卡点它展开当年那张卡。 -->
    <button
      type="button"
      class="accept-bar__status"
      :aria-expanded="expandable ? expanded : undefined"
      :aria-controls="expandable ? 'accept-detail' : undefined"
      :title="
        expandable
          ? expanded
            ? t('work.room.accept.collapse')
            : t('work.room.accept.expand')
          : revoke
            ? undefined
            : t('work.room.accept.review')
      "
      :disabled="revoke"
      @click="onStatus"
    >
      <span v-if="column" class="accept-bar__dot" :style="columnDotStyle(column)" aria-hidden="true" />
      <v-icon v-else :color="color" size="18">{{ icon }}</v-icon>
      <span class="accept-bar__title">{{ title }}</span>
      <v-icon v-if="expandable" size="16" class="accept-bar__caret">{{
        expanded ? 'mdi-chevron-down' : 'mdi-chevron-up'
      }}</v-icon>
    </button>
    <BaseButton v-if="reviewButton" kind="primary" size="sm" @click="emit('review')">
      {{ t('work.room.accept.review') }}
    </BaseButton>
    <template v-else-if="decide">
      <BaseButton kind="secondary" size="sm" prepend-icon="mdi-undo" :disabled="busy" @click="emit('reject')">
        {{ t('work.room.accept.sendBack') }}
      </BaseButton>
      <!-- 采纳 = 当场合并 (#718)：亮在后端会合的那两档，为什么灰写在 title 里。 -->
      <span :title="blockedTitle ?? undefined" class="accept-bar__accept">
        <BaseButton
          kind="primary"
          size="sm"
          prepend-icon="mdi-check"
          :loading="busy"
          :disabled="busy || !!blockedTitle"
          @click="emit('accept')"
        >
          {{ acceptLabel }}
        </BaseButton>
      </span>
    </template>
    <BaseButton v-else-if="revoke" kind="ghost" size="sm" :loading="busy" :disabled="busy" @click="emit('revoke')">
      {{ t('work.room.accept.revoke') }}
    </BaseButton>
  </div>
</template>

<style scoped>
.accept-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 44px;
  padding: 4px 8px 4px 4px;
}
.accept-bar__status {
  display: flex;
  flex: 1 1 auto;
  align-items: center;
  gap: 8px;
  min-width: 0;
  padding: 6px 8px;
  border-radius: var(--radius-md);
  text-align: left;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.accept-bar__status:hover:not(:disabled) {
  background: var(--fill);
}
.accept-bar__status:disabled {
  cursor: default;
}
.accept-bar__title {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--ink);
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
}
/* 「谁的活」的圈。形状和颜色都由 `lib/board.ts` 一处给出（内联样式），这里只管尺寸。 */
.accept-bar__dot {
  flex: 0 0 auto;
  width: 10px;
  height: 10px;
  margin-inline: 4px;
  border: 2px solid var(--faint);
  border-radius: 50%;
}
.accept-bar__caret {
  flex: none;
  margin-left: auto;
  color: var(--faint);
}
.accept-bar__accept {
  flex: none;
}
</style>
