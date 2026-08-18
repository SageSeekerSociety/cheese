<script setup lang="ts">
// 时间刻度: the one archetype in the conversation column that is allowed to be
// CENTERED and to span the full width — because it is not something anyone
// said. Dates, the unread boundary, and 「已拆出子话题」 are marks ON the
// timeline, not entries IN it.
//
// Everything else (messages, platform rows) shares the 54px text axis. Keeping
// that split explicit is the whole point of this component existing: before it,
// centered and left-aligned rows were mixed by accident and the column had
// three different left edges.
defineOptions({ name: 'TimelineMark' })

withDefaults(
  defineProps<{
    /** `unread` 用琥珀，其余中性。 */
    tone?: 'neutral' | 'unread'
    /** 贴在时间线上的一条线（日期）比一条事件（拆出子话题）更安静。 */
    quiet?: boolean
  }>(),
  { tone: 'neutral', quiet: false }
)
</script>

<template>
  <div class="tl-mark" :class="[`tl-mark--${tone}`, { 'tl-mark--quiet': quiet }]">
    <span class="tl-mark__rule" aria-hidden="true" />
    <span class="tl-mark__body"><slot /></span>
    <span class="tl-mark__rule" aria-hidden="true" />
  </div>
</template>

<style scoped>
.tl-mark {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 16px 16px 12px;
}
.tl-mark--quiet {
  margin: 14px 16px 10px;
}
.tl-mark__rule {
  flex: 1 1 auto;
  height: 1px;
  background: var(--line);
}
.tl-mark__body {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  flex: 0 1 auto;
  min-width: 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--faint);
}
/* 新消息线是这一列里唯一一条要被找到的线，所以它是这里唯一带色的一档。 */
.tl-mark--unread .tl-mark__rule {
  background: var(--accent);
  opacity: 0.55;
}
.tl-mark--unread .tl-mark__body {
  color: var(--accent-ink);
  font-weight: 500;
}
</style>
