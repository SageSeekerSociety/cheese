<script setup lang="ts">
// 这台设备上机主自己的 Claude Code（#2991）：登录了没有，以及登录、退出。登录在浏览器里完成，
// 不开终端；「使用 API key」是 Anthropic Console 账号的登录；「使用其他模型服务」填兼容
// Anthropic 接口的服务（GLM、Kimi、自建中转），密钥只存在这台电脑上。
import type { ModelServiceInput } from '@/types/ownAgents'

import { computed, ref } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

defineOptions({ name: 'ClaudeCodeLogin' })

const props = defineProps<{
  loggedIn: boolean
  /** 订阅档位（max、pro），API key 登录时为 null。 */
  plan: string | null
  /** 用的是其他模型服务时，它调用的模型名；用 Claude 账号时为 null。 */
  service?: string | null
  /** 登录进行到哪一步：没在登录、在准备 Claude Code、浏览器里的登录页已打开。 */
  state: 'idle' | 'preparing' | 'browser'
  /** 已登录时给不给「退出登录」。 */
  canLogOut?: boolean
  /** 给不给「使用其他模型服务」。 */
  canUseModelService?: boolean
}>()

const emit = defineEmits<{ login: [console: boolean]; service: [ModelServiceInput]; cancel: []; logout: [] }>()

// 订阅档位按厂商的写法首字母大写（max → Max）；API key 登录没有档位。
function planLabel(plan: string | null) {
  return plan ? plan.charAt(0).toUpperCase() + plan.slice(1) : t('account.devices.claudeCodeApiKey')
}

const filling = ref(false)
const baseUrl = ref('')
const token = ref('')
const model = ref('')

// 地址要是 http(s) 网址，密钥和模型名都要有，模型名是一个词：和连接程序保存前查的一样。
const complete = computed(
  () =>
    /^https?:\/\/[^\s/]+/.test(baseUrl.value.trim()) && token.value.trim() !== '' && /^\S+$/.test(model.value.trim())
)

function save() {
  if (!complete.value) return
  emit('service', { baseUrl: baseUrl.value.trim(), token: token.value.trim(), model: model.value.trim() })
  filling.value = false
  token.value = ''
}

const loggedInLabel = computed(() =>
  props.service
    ? t('account.thisDevice.claudeModelService', { model: props.service })
    : t('account.thisDevice.claudeLoggedIn', { plan: planLabel(props.plan) })
)
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
      <span class="cc__ok">{{ loggedInLabel }}</span>
      <BaseButton v-if="canLogOut" kind="ghost" size="sm" @click="emit('logout')">
        {{ t('account.thisDevice.claudeLogout') }}
      </BaseButton>
    </div>
    <div v-else-if="filling" class="cc__form">
      <v-text-field
        v-model="baseUrl"
        :label="t('account.thisDevice.modelServiceUrl')"
        placeholder="https://"
        density="compact"
        variant="outlined"
        hide-details
        autocomplete="off"
      />
      <v-text-field
        v-model="token"
        :label="t('account.thisDevice.modelServiceToken')"
        type="password"
        density="compact"
        variant="outlined"
        hide-details
        autocomplete="off"
      />
      <v-text-field
        v-model="model"
        :label="t('account.thisDevice.modelServiceModel')"
        density="compact"
        variant="outlined"
        hide-details
        autocomplete="off"
      />
      <div class="cc__row">
        <BaseButton kind="secondary" :disabled="!complete" @click="save">{{
          t('account.thisDevice.modelServiceSave')
        }}</BaseButton>
        <BaseButton kind="ghost" size="sm" @click="filling = false">{{ t('account.thisDevice.cancel') }}</BaseButton>
      </div>
      <div class="cc__hint">{{ t('account.thisDevice.modelServiceHint') }}</div>
    </div>
    <div v-else class="cc__row">
      <BaseButton kind="secondary" @click="emit('login', false)">{{ t('account.thisDevice.claudeLogin') }}</BaseButton>
      <BaseButton kind="ghost" size="sm" @click="emit('login', true)">{{
        t('account.thisDevice.claudeApiKey')
      }}</BaseButton>
      <BaseButton v-if="canUseModelService" kind="ghost" size="sm" @click="filling = true">{{
        t('account.thisDevice.modelServiceOpen')
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

.cc__form {
  display: flex;
  flex-direction: column;
  /* 标签浮在框的上边线上，叠着的输入框之间要留出它的位置 */
  gap: 16px;
  max-width: 420px;
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
