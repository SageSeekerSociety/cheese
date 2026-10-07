<script setup lang="ts">
// 这台设备上机主自己的 Claude Code（#2991）：登录了没有，以及登录、退出。登录在浏览器里完成，
// 不开终端；「使用 API key」是 Anthropic Console 账号的登录。
import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

defineOptions({ name: 'ClaudeCodeLogin' })

defineProps<{
  loggedIn: boolean
  /** 订阅档位（max、pro），API key 登录时为 null。 */
  plan: string | null
  /** 登录进行到哪一步：没在登录、在准备 Claude Code、浏览器里的登录页已打开。 */
  state: 'idle' | 'preparing' | 'browser'
  /** 已登录时给不给「退出登录」。 */
  canLogOut?: boolean
}>()

const emit = defineEmits<{ login: [console: boolean]; cancel: []; logout: [] }>()

// 订阅档位按厂商的写法首字母大写（max → Max）；API key 登录没有档位。
function planLabel(plan: string | null) {
  return plan ? plan.charAt(0).toUpperCase() + plan.slice(1) : t('account.devices.claudeCodeApiKey')
}
</script>

<template>
  <div class="cc">
    <div v-if="state !== 'idle'" class="cc__row" role="status">
      <v-progress-circular indeterminate size="16" width="2" />
      <span>{{
        state === 'preparing' ? t('account.thisDevice.claudePreparing') : t('account.thisDevice.claudeBrowser')
      }}</span>
      <BaseButton kind="ghost" size="sm" @click="emit('cancel')">{{ t('account.thisDevice.cancel') }}</BaseButton>
    </div>
    <div v-else-if="loggedIn" class="cc__row">
      <span class="cc__ok">{{ t('account.thisDevice.claudeLoggedIn', { plan: planLabel(plan) }) }}</span>
      <BaseButton v-if="canLogOut" kind="ghost" size="sm" @click="emit('logout')">
        {{ t('account.thisDevice.claudeLogout') }}
      </BaseButton>
    </div>
    <div v-else class="cc__row">
      <BaseButton kind="secondary" @click="emit('login', false)">{{ t('account.thisDevice.claudeLogin') }}</BaseButton>
      <BaseButton kind="ghost" size="sm" @click="emit('login', true)">{{
        t('account.thisDevice.claudeApiKey')
      }}</BaseButton>
    </div>
    <div class="cc__hint">{{ t('account.thisDevice.claudeHint') }}</div>
  </div>
</template>

<style scoped>
.cc {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.cc__row {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  font-size: 14px;
  line-height: var(--lh-14);
}

.cc__ok {
  color: var(--ok-ink);
}

.cc__hint {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
</style>
