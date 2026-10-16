<script setup lang="ts">
/**
 * 「本轮运行时间可能较长，完成后通知你」——问推送权限的那一条（#1084 第 5 步）。
 *
 * 什么时候问、问过没有、点了「开启通知」做什么，都在
 * `composables/usePushPermissionPrompt.ts` 那一层：组件不直接碰 `services/webPush`
 * （`.claude/rules/architecture.md` 的组件边界）。这里只管画那一条和两颗按钮。
 */
import { usePushPermissionPrompt } from '@/composables/usePushPermissionPrompt'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const props = defineProps<{
  /** 芝士此刻是不是在这个房间里运行（TopicView 按轮次生命周期给）。 */
  working: boolean
}>()

const { shown, busy, accept, decline } = usePushPermissionPrompt(() => props.working)
</script>

<template>
  <!-- 它插在话题头和面板之间，出现时下面整个房间让出一条。0.2s 折叠，看得出是
       这里多了一条，而不是整页往下跳了一截；外边距在折叠的那一层里面，跟着一起长。 -->
  <Transition name="push-fold">
    <div v-if="shown" class="push-fold">
      <div class="push-fold__inner">
        <div class="push-ask">
          <span class="push-ask__text">{{ t('work.push.ask') }}</span>
          <div class="push-ask__actions">
            <BaseButton kind="primary" size="sm" :loading="busy" @click="accept">{{
              t('work.push.enable')
            }}</BaseButton>
            <BaseButton kind="ghost" size="sm" :disabled="busy" @click="decline">{{
              t('work.push.decline')
            }}</BaseButton>
          </div>
        </div>
      </div>
    </div>
  </Transition>
</template>

<style scoped>
.push-fold {
  display: grid;
  flex: 0 0 auto;
  grid-template-rows: 1fr;
}
.push-fold__inner {
  min-height: 0;
}
.push-fold-enter-active,
.push-fold-leave-active {
  transition:
    grid-template-rows 0.2s ease,
    opacity 0.2s ease;
}
.push-fold-enter-active .push-fold__inner,
.push-fold-leave-active .push-fold__inner {
  overflow: hidden;
}
.push-fold-enter-from,
.push-fold-leave-to {
  grid-template-rows: 0fr;
  opacity: 0;
}
.push-ask {
  margin: 8px 12px 0;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--surface);
}

.push-ask__text {
  color: var(--text);
  font-size: 13px;
}

.push-ask__actions {
  display: flex;
  align-items: center;
  gap: 4px;
}
</style>
