<script setup lang="ts">
// 时间线上「谁做了一件事」那一行的外框（改了几个文件、记了一条决策、检查没过、
// 运行环境就绪）。
//
// 它是一行，不是一条消息：记号落在消息的头像列里，缩小一号，事件那一行接在后面，
// 正文和消息正文同一条竖线，时间在行尾。记号只回答「这是谁的事」——AI 队友的就是
// 它的头像（名字在 title 里，事件文字里需要的时候自己会写），平台自己的就是一个
// 中性的记号。轻重不归记号管，归这一行的底色（RoomNotice 的 `sys-row--warn/danger`）。
import CheeseAvatar from './CheeseAvatar.vue'

import { t } from '@/i18n'

defineProps<{ name: string | null; time: string }>()
</script>

<template>
  <div class="notice-row" :class="{ 'agent-status': name }">
    <span v-if="name" class="notice-row__mark" :title="name" :aria-label="name" role="img">
      <CheeseAvatar :size="20" :name="name" />
    </span>
    <span v-else class="notice-row__mark" :title="t('work.room.notice.platform')">
      <span class="notice-row__platform" aria-hidden="true">
        <v-icon size="12">mdi-server</v-icon>
      </span>
    </span>
    <div class="notice-row__body"><slot /></div>
    <span class="notice-row__time">{{ time }}</span>
  </div>
</template>

<style scoped>
/* 左右和消息行对齐：头像列 16px 起、28px 宽（20px 的记号在里面居中），正文从
   54px 起，和消息正文同一条竖线。 */
.notice-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 2px 16px 2px 20px;
  margin-top: 8px;
}
/* 连着几件事贴在一起，和同一个人连着说的几句话一样。 */
.notice-row + .notice-row {
  margin-top: 2px;
}
.notice-row__mark {
  display: inline-flex;
  flex: none;
}
.notice-row__platform {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: var(--radius-sm);
  background: var(--fill-2);
  color: var(--muted);
}
.notice-row__body {
  flex: 1;
  min-width: 0;
  padding-left: 4px;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
.notice-row__time {
  flex: none;
  color: var(--faint);
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: var(--lh-12);
  padding-top: 1px; /* 18px 的行盒放进 20px 的记号高度里，上下各 1px */
}
</style>
