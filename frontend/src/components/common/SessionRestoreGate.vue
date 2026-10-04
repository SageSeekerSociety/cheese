<script setup lang="ts">
// 冷打开时，手里有一份本地会话、但要去服务端确认的那一段时间的界面。
//
// 纯展示：状态和动作都由 views 那一层通过 useSessionRestore 给进来（组件边界规则
// 不允许 src/components 下的文件碰 services/router）。文案见 shell.restore.*。
import type { RestorePhase } from '@/composables/useSessionRestore'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

defineProps<{
  visible: boolean
  phase: RestorePhase
  retrying: boolean
  navigationFailed: boolean
}>()

defineEmits<{ retry: []; continue: [] }>()
</script>

<template>
  <Transition name="restore-gate">
    <div v-if="visible" class="restore-gate" role="status" aria-live="polite">
      <div class="restore-gate__card">
        <v-progress-circular v-if="phase === 'restoring'" indeterminate size="28" width="3" color="primary" />
        <v-icon v-else size="28" color="primary">mdi-wifi-alert</v-icon>
        <p class="restore-gate__title">
          {{ phase === 'restoring' ? t('shell.restore.restoring') : t('shell.restore.unreachable') }}
        </p>
        <p v-if="phase === 'unreachable'" class="restore-gate__hint c-muted">
          {{ t('shell.restore.unreachableHint') }}
        </p>
        <p v-if="navigationFailed" class="restore-gate__hint c-muted">{{ t('shell.restore.navigationFailed') }}</p>
        <div v-if="phase === 'unreachable'" class="restore-gate__actions">
          <BaseButton kind="primary" :loading="retrying" @click="$emit('retry')">{{
            t('shell.restore.retry')
          }}</BaseButton>
          <BaseButton kind="ghost" @click="$emit('continue')">{{ t('shell.restore.continueAsGuest') }}</BaseButton>
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
