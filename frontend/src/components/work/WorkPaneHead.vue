<script setup lang="ts">
// 频道页上工作面板一格的标题栏里面板那一段（`WorkLayout.single`）：从别处打开的那一格（一份文件、
// 一件任务）前面一颗「返回」，然后是它的名字。名字后面的放大、✕ 是布局层画的。
//
// 不给 `title` 时名字那一段留成一个落点（`outlet`）：在频道里打开的一件任务把它自己的页头
// （任务名、状态、退回 / 采纳、打开任务）放进来，和「返回」同一行、排在它后面。
//
// 任务页「对话」那一种摆法里一块的标题栏也是它画的：名字那一段可以换成别的（`title` 插槽，「文件」
// 那一块是路径），名字后面紧跟着的（`after-title`，终端那一块换终端），这一块自己的按钮靠右
// （默认插槽），再往右是布局层的放大、✕。
import { onBeforeUnmount, ref, watch } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

defineProps<{
  /** 布局区给这一格标题栏的落点；还没有就不画。 */
  target?: HTMLElement
  /** 这一格是从别处打开的：前面有「返回」。 */
  back?: boolean
  /** 这一格的名字；不给就留一个落点给别人填。 */
  title?: string
}>()

const emit = defineEmits<{
  (e: 'back'): void
  /** 名字那一段的落点，有了、换了、没了。 */
  (e: 'outlet', element: HTMLElement | null): void
}>()

const slots = defineSlots<{
  /** 代替名字的那一段。 */
  title?: () => unknown
  /** 紧跟在名字后面的。 */
  'after-title'?: () => unknown
  /** 这一格自己的按钮，靠右。 */
  default?: () => unknown
}>()

const outlet = ref<HTMLElement | null>(null)
watch(outlet, (element) => emit('outlet', element), { flush: 'post' })
onBeforeUnmount(() => emit('outlet', null))
</script>

<template>
  <Teleport v-if="target" :to="target">
    <BaseButton
      v-if="back"
      kind="ghost"
      size="sm"
      icon="mdi-arrow-left"
      :aria-label="t('global.back')"
      :title="t('global.back')"
      data-pane-back
      @click="emit('back')"
    />
    <slot v-if="slots.title" name="title" />
    <span v-else-if="title !== undefined" class="work-pane-head__title" :title="title">{{ title }}</span>
    <span v-else ref="outlet" class="work-pane-head__outlet" />
    <slot name="after-title" />
    <span v-if="slots.default" class="work-pane-head__tools"><slot /></span>
  </Teleport>
</template>

<style scoped>
.work-pane-head__title {
  min-width: 0;
  padding: 0 8px;
  overflow: hidden;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.work-pane-head__tools {
  display: flex;
  flex: none;
  gap: 2px;
  align-items: center;
  margin-left: auto;
}

.work-pane-head__outlet {
  display: flex;
  flex: 1 1 auto;
  align-items: center;
  min-width: 0;
  height: 100%;
}
</style>
