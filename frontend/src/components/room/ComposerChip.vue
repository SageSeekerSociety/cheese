<script setup lang="ts">
// 输入框里跟着这条消息一起走的一样东西：回复的那条、待发的一个附件。
//
// 两样用同一种标签：左边一个记号说它是什么，中间是名字，右边一个 × 把它拿掉。
// 回复原来是输入框上面单独的一条横栏，附件是框里一排带边框的方块——同一件事
// （「这条消息还带着什么」）两种长相，而方块的边框套在输入框的边框里，是框中框。
defineProps<{
  /** 名字。标签窄，放不下就截断，全文在悬停气泡里。 */
  label: string
  /** × 的读屏标签：拿掉的是哪一个。 */
  removeLabel: string
  /** 回复那一枚：字淡一档，它说的是上下文，不是这条消息带着的东西。 */
  quiet?: boolean
}>()
const emit = defineEmits<{ (e: 'remove'): void }>()
</script>

<template>
  <span class="chip" :class="{ 'chip--quiet': quiet }">
    <span class="chip__face"><slot name="face" /></span>
    <span class="chip__label">{{ label }}</span>
    <!-- 名字在标签边缘就截断了，全名得有地方看。不用 title：系统原生气泡要停约
         一秒才弹，又是屏幕上唯一不跟随主题的东西。 -->
    <v-tooltip activator="parent" location="top" :text="label" />
    <button type="button" class="chip__x" :aria-label="removeLabel" @click="emit('remove')">
      <v-icon size="12">mdi-close</v-icon>
    </button>
  </span>
</template>

<style scoped>
/* 28px 高，和动作行的按钮一个高度；底色而不是边框，框里不再套框。 */
.chip {
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: 6px;
  max-width: 280px;
  height: 28px;
  padding: 0 2px 0 4px;
  border-radius: var(--radius-md);
  background: var(--fill);
  color: var(--text);
  font-size: 12px;
  line-height: var(--lh-12);
}
.chip--quiet {
  max-width: 320px;
  color: var(--muted);
}
/* 记号占 20px 见方，在 28px 高的标签里上下各留 4px。 */
.chip__face {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  color: var(--faint);
}
/* 附件的缩略图（.att-face，style.css）在别处是 40px，这里缩进 20px 的记号格。 */
.chip__face :deep(.att-face) {
  width: 20px;
  height: 20px;
}
.chip__label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.chip__x {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: var(--radius-sm);
  color: var(--faint);
  cursor: pointer;
  transition:
    background-color var(--dur-quick) var(--ease-standard),
    color var(--dur-quick) var(--ease-standard);
}
.chip__x:hover {
  background: var(--line-2);
  color: var(--ink);
}
</style>
