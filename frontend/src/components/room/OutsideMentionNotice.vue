<script setup lang="ts">
// 刚发出去的那条 @ 了不在这个话题里的人：一行说明，贴在输入框上方。
//
// 它只吃 props、只往上发事件：谁不在、能不能拉、拉的时候在不在忙，全在
// `composables/useOutsideMentionPrompt.ts` 里算好。按钮只给能管名册的人（owner /
// admin）；别人只看到那句话——按钮按下去后端也会拒，不如不给。
import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

defineProps<{
  /** 不在话题里的那几个人，已经连成一串的名字。 */
  names: string
  /** 能不能把他们拉进来（owner / admin）。 */
  canAdd: boolean
  busy: boolean
  /** 拉人失败时说给人听的那一句。 */
  error: string
}>()

const emit = defineEmits<{
  (e: 'add'): void
  (e: 'dismiss'): void
}>()
</script>

<template>
  <div class="outside-notice" role="status">
    <v-icon size="15" class="outside-notice__icon">mdi-account-alert-outline</v-icon>
    <span class="outside-notice__text">{{ t('work.room.mention.outsideNotice', { names }) }}</span>
    <span v-if="error" class="outside-notice__error">{{ error }}</span>
    <BaseButton
      v-if="canAdd"
      kind="secondary"
      size="sm"
      class="outside-notice__add"
      :loading="busy"
      @click="emit('add')"
    >
      {{ t('work.room.mention.addToTopic') }}
    </BaseButton>
    <button
      type="button"
      class="outside-notice__dismiss tap-target"
      :aria-label="t('work.room.mention.dismissOutside')"
      :title="t('work.room.mention.dismissOutside')"
      @click="emit('dismiss')"
    >
      <v-icon size="14">mdi-close</v-icon>
    </button>
  </div>
</template>

<style scoped>
/* 一句说明，不是一张卡：没有边框和投影，只垫一层浅底和输入框分开。 */
.outside-notice {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
  padding: 6px 8px 6px 10px;
  border-radius: var(--radius-sm);
  background: var(--fill);
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
.outside-notice__icon {
  flex: none;
  color: var(--muted);
}
.outside-notice__text {
  flex: 1 1 auto;
  min-width: 0;
}
.outside-notice__error {
  color: var(--danger-ink);
}
.outside-notice__add {
  flex: none;
}
.outside-notice__dismiss {
  position: relative;
  flex: none;
  display: inline-flex;
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
.outside-notice__dismiss:hover {
  background: var(--line);
  color: var(--muted);
}
</style>
