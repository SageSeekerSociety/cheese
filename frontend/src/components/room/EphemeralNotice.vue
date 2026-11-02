<script setup lang="ts">
// 「仅你可见」的一条：AI 队友只对此刻这个人说的话，挂在时间线末尾。
//
// 它不是消息：不落库，别人看不到，刷新之后也不在（要说它的那一方只在给这个人的回答里
// 带着它，比如发送接口回的 `unasked`）。长得像队友说的一条（头像、名字），底色浅一档、
// 名字旁写着「仅你可见」，一眼分得出是平台对你一个人说的，不是频道里谁说的话。
// 正文和按钮由用的地方给：按钮按完它就该消失，消失由用的地方决定（它不存状态）。
import CheeseAvatar from '@/components/CheeseAvatar.vue'
import { t } from '@/i18n'

defineProps<{
  /** 说这句话的 AI 队友（席位和界面上的名字）。 */
  agent: { handle: string; label: string }
}>()
</script>

<template>
  <div class="ephemeral" role="status">
    <CheeseAvatar :size="28" :name="agent.label" :handle="agent.handle" />
    <div class="ephemeral__main">
      <div class="ephemeral__meta">
        <span class="ephemeral__name">{{ agent.label }}</span>
        <span class="ephemeral__tag">
          <v-icon size="13" aria-hidden="true">mdi-eye-outline</v-icon>
          {{ t('work.room.ephemeral.onlyYou') }}
        </span>
      </div>
      <div class="ephemeral__text"><slot /></div>
      <div v-if="$slots.actions" class="ephemeral__actions"><slot name="actions" /></div>
    </div>
  </div>
</template>

<style scoped>
/* 和一条消息同一套几何（room-row.css：左右 16px、头像 28px、间距 10px），底色浅一档。 */
.ephemeral {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin: 8px 16px 12px;
  padding: 8px 10px;
  border-radius: var(--radius-md);
  background: var(--fill);
}
.ephemeral__main {
  min-width: 0;
  flex: 1 1 auto;
}
.ephemeral__meta {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 2px;
}
.ephemeral__name {
  font-size: 13px;
  line-height: var(--lh-13);
  font-weight: 600;
  color: var(--ink);
}
.ephemeral__tag {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}
.ephemeral__text {
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--text);
}
.ephemeral__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
}
</style>
