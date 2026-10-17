<script setup lang="ts">
// 冷打开时，手里有一份本地会话、服务端却确认不了它的时候的界面：连不上，可以重试，
// 或者先以访客身份进去。正在确认的那一段不在这里，启动骨架一直盖着（main.ts）。
//
// 纯展示：状态和动作都由 views 那一层通过 useSessionRestore 给进来（组件边界规则
// 不允许 src/components 下的文件碰 services/router）。文案见 shell.restore.*。
import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

defineProps<{
  visible: boolean
  /** 人点了重试（或网络回来自动重试），正在等结果。 */
  retrying: boolean
  /** 恢复成功了，只是路由那一下没走成（人已经登上了，页面还停在旧的那一页）。 */
  navigationFailed: boolean
}>()

defineEmits<{ retry: []; continue: [] }>()
</script>

<template>
  <Transition name="restore-gate">
    <div v-if="visible" class="restore-gate" role="status" aria-live="polite">
      <div class="restore-gate__card">
        <v-icon size="28" color="primary">mdi-wifi-alert</v-icon>
        <!-- Two different "not yet": someone already signed in must not read "can't confirm your session". -->
        <p class="restore-gate__title">
          {{ navigationFailed ? t('shell.restore.navigationFailed') : t('shell.restore.unreachable') }}
        </p>
        <p v-if="!navigationFailed" class="restore-gate__hint c-muted">{{ t('shell.restore.unreachableHint') }}</p>
        <div class="restore-gate__actions">
          <BaseButton kind="primary" :loading="retrying" @click="$emit('retry')">{{
            t('shell.restore.retry')
          }}</BaseButton>
          <BaseButton kind="ghost" :disabled="retrying" @click="$emit('continue')">{{
            t('shell.restore.continueAsGuest')
          }}</BaseButton>
        </div>
      </div>
    </div>
  </Transition>
</template>

<style lang="scss" scoped>
.restore-gate {
  position: fixed;
  inset: 0;
  /* 和 OfflineBanner 同一档：压在顶栏、抽屉、对话框之上。 */
  z-index: var(--z-banner);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: rgb(var(--v-theme-background));
}
.restore-gate__card {
  display: flex;
  flex-direction: column;
  gap: 12px;
  align-items: center;
  max-width: 340px;
  padding: 28px 24px;
  text-align: center;
  background: rgb(var(--v-theme-surface));
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-2);
}
.restore-gate__title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
}
.restore-gate__hint {
  margin: 0;
  font-size: 13px;
  line-height: 1.5;
}
.restore-gate__actions {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-top: 4px;
}
.restore-gate-enter-active,
.restore-gate-leave-active {
  transition: opacity var(--dur-base) var(--ease-standard);
}
.restore-gate-enter-from,
.restore-gate-leave-to {
  opacity: 0;
}
</style>
