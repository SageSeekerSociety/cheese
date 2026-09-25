<script setup lang="ts">
// 平台替 AI 队友写下的一条（改了几个文件、记了一条决策、运行环境就绪）的外框。
//
// 它是一行，不是一条消息：头像落在消息的头像列里，缩小一号，事件那一行接在后面，
// 时间在行尾。原来这里画成一条完整的消息——头像、名字、「运行状态」、时间一行，
// 下面再套一个描边的框——而框里那一行自己又写着「芝士 记录了决策」：同一个名字说
// 两遍，一件小事占了一条消息的高度。名字不再单写一遍：头像上是它的字，名字在
// title 里，事件文字里需要的时候自己会写。
import CheeseAvatar from './CheeseAvatar.vue'

defineProps<{ name: string | null; time: string }>()
</script>

<template>
  <div v-if="name" class="agent-status">
    <span class="agent-status-face" :title="name" :aria-label="name" role="img">
      <CheeseAvatar :size="20" :name="name" />
    </span>
    <div class="agent-status-body"><slot /></div>
    <span class="agent-status-time">{{ time }}</span>
  </div>
  <slot v-else />
</template>

<style scoped>
/* 左右和消息行对齐：头像列 16px 起、28px 宽（20px 的头像在里面居中），正文从
   54px 起，和消息正文同一条竖线。 */
.agent-status {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 2px 16px 2px 20px;
  margin-top: 4px;
}
.agent-status-face {
  display: inline-flex;
  flex: none;
  padding-top: 1px;
}
.agent-status-body {
  flex: 1;
  min-width: 0;
  padding-left: 4px;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
.agent-status-time {
  flex: none;
  padding-top: 1px;
  color: var(--faint);
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: var(--lh-12);
}
.agent-status-body :deep(.sys-row) {
  padding: 0;
}
/* 头像已经说了「这是一条事件、是它的」，行首那颗圆点就多余了；下面的第二行跟着
   不再为它缩进。报错、事故那几档的记号是图标，说的是轻重，留着。 */
.agent-status-body :deep(.sys-mark--dot) {
  display: none;
}
.agent-status-body :deep(.sys-sub) {
  padding-left: 0;
}
.agent-status-body :deep(.sys-line) {
  justify-content: flex-start;
  flex-wrap: wrap;
}
.agent-status-body :deep(.sys-text) {
  min-width: 0;
  overflow-wrap: anywhere;
}
.agent-status-body :deep(.sys-detail) {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
