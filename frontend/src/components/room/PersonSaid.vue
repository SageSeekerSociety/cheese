<script setup lang="ts">
// 一个人借平台的一个动作说的话：审阅的人退回时写下的理由。它是那个人对 AI 队友说的
// 一句话，所以画成那个人的一条消息——头像、名字、时间，正文就是理由原文；正文上面
// 一小行说是在做什么（「退回了改动」），和回复一条消息时那一小行同一个位置、同一个样子。
// 数据上它仍是那一条退回记录，不另发一条消息。
import UserAvatar from '../common/UserAvatar.vue'

defineProps<{
  /** 说这句话的人。 */
  handle: string
  name: string
  time: string
  /** 正文上面那一小行：在做什么。 */
  cue: string
  /** 这个人写下的原话。 */
  said: string
}>()
</script>

<template>
  <div class="im-row person-said" data-testid="person-said">
    <div class="im-gutter">
      <button type="button" class="im-person" :data-handle="handle" :aria-label="name">
        <UserAvatar avatar="" :name="name" :seed="handle" :size="28" />
      </button>
    </div>
    <div class="im-main">
      <div class="im-meta">
        <button type="button" class="im-name im-person" :data-handle="handle">{{ name }}</button>
        <span class="im-time">{{ time }}</span>
      </div>
      <div class="person-said__cue">
        <v-icon size="12" aria-hidden="true">mdi-undo-variant</v-icon>
        <span>{{ cue }}</span>
      </div>
      <div class="im-text person-said__text" data-user-content>{{ said }}</div>
    </div>
  </div>
</template>

<style scoped>
/* 和消息回复时那一小行（RoomMessage 的 .im-replied）同一个样子。 */
.person-said__cue {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  max-width: 100%;
  margin-bottom: 3px;
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  background: var(--fill);
  font-size: 12px;
  color: var(--muted);
}
.person-said__text {
  white-space: pre-wrap;
}
</style>
<style scoped src="./room-row.css"></style>
