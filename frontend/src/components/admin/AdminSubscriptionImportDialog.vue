<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import {
  cancelSubscriptionDeviceFlow,
  type DeviceFlowStartResponse,
  type LlmSubscription,
  pollSubscriptionDeviceFlow,
  startSubscriptionDeviceFlow,
} from '@/api'

// 「导入订阅」对话框：device-code 授权的状态机。
//
// 状态（四态 + 过程态分开画，一个都不许借另一个的画法）：
//
//   start    说明 + 备注名（可选）+「开始授权」。琥珀在对话框内：页面上的琥珀
//            仍是「新增模型」，对话框是独立一层上下文，同一屏不同时可见两个。
//   waiting  user_code 大字等宽 + 复制 + 打开授权页 + 倒计时 + 轮询脉冲。
//            脉冲点动画带 prefers-reduced-motion 开关 —— 关掉后「正在等待
//            授权…」的文字还在，信息不靠动画承载。
//   success  「订阅已连接」+ 账号 email +「完成」；关闭后 emit `imported`。
//   expired  「代码已过期」+「重新开始」（回到 start 重新发 flow）。
//   error    服务端原话 +「重试」。框不关 —— 关掉等于把「为什么没成」丢掉。
//
// 轮询纪律：`setInterval(max(interval, 3) * 1000)`，pending 继续、complete 进
// success、expired 进 expired；**对话框关闭就 clearInterval** —— 服务端的 flow
// 会自己过期，页面不留一个空转的定时器。取消是真的发 cancel 请求再关。
//
// 定向重授权（`targetSubscriptionId` 非空）：start 态文案换成「重新授权你的
// ChatGPT 账号」；服务端校验不同账号会 400，那句原话落在 error 态。
//
// 界面不说实现词：没有「device flow」「OAuth」，只有「授权」「连接」（§8）。

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    /** 定向重授权的旧订阅 id；空 = 新导入。 */
    targetSubscriptionId?: string | null
  }>(),
  { targetSubscriptionId: null }
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'imported', subscription: LlmSubscription): void
}>()

type Phase = 'start' | 'waiting' | 'success' | 'expired' | 'error'

const { t } = useI18n()

const phase = ref<Phase>('start')
const label = ref('')
/** 上游模型输入框的原样值；空 = 跟随部署默认（提交时发 null）。 */
const upstreamModel = ref('')
const starting = ref(false)
const flow = ref<DeviceFlowStartResponse | null>(null)
const subscription = ref<LlmSubscription | null>(null)
const errorText = ref<string | null>(null)
/** error 态的「重试」重做什么：start 阶段的重发 flow，waiting 阶段的恢复轮询。 */
const retryWhat = ref<'start' | 'poll'>('start')
const copied = ref(false)
/** 倒计时（秒）。waiting 期间每秒减一，到 0 本地进 expired（服务端也是这个答案）。 */
const remainingS = ref(0)

let pollTimer: ReturnType<typeof setInterval> | null = null
let tickTimer: ReturnType<typeof setInterval> | null = null
let copyTimer: ReturnType<typeof setTimeout> | null = null
/** 关闭对话框后迟到的请求答案不许再改状态（定时器已清，但 Promise 不管开关）。 */
let closed = true

const minutesLeft = computed(() => Math.max(1, Math.ceil(remainingS.value / 60)))

/** 上游模型只拒「输入了但全是空白」：留空合法（跟随部署默认）。 */
const upstreamOk = computed(() => {
  const v = upstreamModel.value
  return v === '' || v.trim() !== ''
})

const isReauth = computed(() => !!props.targetSubscriptionId)

function stopTimers() {
  if (pollTimer !== null) clearInterval(pollTimer)
  if (tickTimer !== null) clearInterval(tickTimer)
  pollTimer = null
  tickTimer = null
}

function reset() {
  stopTimers()
  if (copyTimer !== null) clearTimeout(copyTimer)
  copyTimer = null
  phase.value = 'start'
  label.value = ''
  upstreamModel.value = ''
  flow.value = null
  subscription.value = null
  errorText.value = null
  retryWhat.value = 'start'
  copied.value = false
  remainingS.value = 0
}

watch(
  () => props.modelValue,
  (open) => {
    closed = !open
    if (open) reset()
    else stopTimers()
  },
  { immediate: true }
)

onBeforeUnmount(() => {
  closed = true
  stopTimers()
  if (copyTimer !== null) clearTimeout(copyTimer)
})

function serverWords(e: unknown): string {
  return e instanceof Error && e.message ? e.message : t('models.subscription.failed')
}

async function begin() {
  if (starting.value) return
  starting.value = true
  errorText.value = null
  try {
    const started = await startSubscriptionDeviceFlow({
      provider: 'openai_codex',
      label: label.value.trim() || null,
      target_subscription_id: props.targetSubscriptionId ?? null,
      upstream_model: upstreamModel.value.trim() || null,
    })
    if (closed) return
    flow.value = started
    remainingS.value = started.expires_in
    phase.value = 'waiting'
    startPolling(started)
  } catch (e) {
    if (closed) return
    // 失败框不关、原因照原话 —— 关掉等于把刚填的备注名和「为什么退回」一起丢掉。
    errorText.value = serverWords(e)
    retryWhat.value = 'start'
    phase.value = 'error'
  } finally {
    starting.value = false
  }
}

function startPolling(current: DeviceFlowStartResponse) {
  stopTimers()
  const everyMs = Math.max(current.interval, 3) * 1000
  pollTimer = setInterval(() => void pollOnce(), everyMs)
  tickTimer = setInterval(() => {
    remainingS.value = Math.max(0, remainingS.value - 1)
    if (remainingS.value === 0 && phase.value === 'waiting') {
      stopTimers()
      phase.value = 'expired'
    }
  }, 1000)
}

let polling = false

async function pollOnce() {
  const current = flow.value
  if (!current || polling || phase.value !== 'waiting') return
  polling = true
  try {
    const result = await pollSubscriptionDeviceFlow(current.flow_id)
    if (closed || phase.value !== 'waiting') return
    if (result.state === 'pending') return
    stopTimers()
    if (result.state === 'expired') {
      phase.value = 'expired'
      return
    }
    subscription.value = result.subscription
    phase.value = 'success'
  } catch (e) {
    if (closed) return
    stopTimers()
    errorText.value = serverWords(e)
    retryWhat.value = 'poll'
    phase.value = 'error'
  } finally {
    polling = false
  }
}

function retry() {
  errorText.value = null
  if (retryWhat.value === 'poll' && flow.value && remainingS.value > 0) {
    phase.value = 'waiting'
    startPolling(flow.value)
    return
  }
  phase.value = 'start'
}

async function copyCode() {
  const code = flow.value?.user_code
  if (!code) return
  try {
    await navigator.clipboard.writeText(code)
    copied.value = true
    if (copyTimer !== null) clearTimeout(copyTimer)
    copyTimer = setTimeout(() => {
      copied.value = false
    }, 2000)
  } catch {
    // 剪贴板不可用（非安全上下文 / 权限被拒）不是要弹窗的错 —— 那串代码就在屏幕上。
  }
}

async function cancelImport() {
  stopTimers()
  const current = flow.value
  close()
  // 取消真的要告诉服务端（否则这条 pending 行要等 15 分钟自己过期）；失败不拦
  // 关闭 —— 服务端 flow 反正会自灭，而「关掉这个框」是人的第一诉求。
  if (current) {
    try {
      await cancelSubscriptionDeviceFlow(current.flow_id)
    } catch {
      /* 见上：不拦关闭 */
    }
  }
}

function finish() {
  const done = subscription.value
  close()
  if (done) emit('imported', done)
}

function close() {
  emit('update:modelValue', false)
}
</script>

<template>
  <v-dialog :model-value="modelValue" max-width="440" @update:model-value="!$event && close()">
    <v-card rounded="lg">
      <v-card-title class="px-4 pt-4 pb-2 asid__title">{{ t('models.subscription.dialogTitle') }}</v-card-title>

      <!-- start：说明 + 备注名 + 开始授权。定向重授权时换「重新授权」那句。 -->
      <template v-if="phase === 'start'">
        <v-card-text class="px-4">
          <p class="asid__intro">
            {{ isReauth ? t('models.subscription.reauthIntro') : t('models.subscription.intro') }}
          </p>
          <v-text-field
            v-model="label"
            :label="t('models.subscription.label')"
            :hint="t('models.subscription.labelHint')"
            density="comfortable"
            variant="outlined"
            maxlength="200"
            autocomplete="off"
            hide-details="auto"
          />
          <v-text-field
            v-model="upstreamModel"
            :label="t('models.subscription.upstreamModel')"
            :hint="t('models.subscription.upstreamModelHint')"
            :error="!upstreamOk"
            :error-messages="upstreamOk ? [] : [t('models.subscription.upstreamModelInvalid')]"
            density="comfortable"
            variant="outlined"
            maxlength="200"
            autocomplete="off"
            hide-details="auto"
            class="mt-3"
            data-testid="upstream-model-input"
          />
        </v-card-text>
        <v-card-actions class="pa-4 pt-0">
          <v-spacer />
          <v-btn variant="text" :disabled="starting" @click="close">{{ t('models.dialog.cancel') }}</v-btn>
          <v-btn color="primary" :loading="starting" :disabled="!upstreamOk" @click="begin">{{
            t('models.subscription.start')
          }}</v-btn>
        </v-card-actions>
      </template>

      <!-- waiting：代码大字 + 复制 + 授权页链接 + 倒计时 + 轮询脉冲。 -->
      <template v-else-if="phase === 'waiting' && flow">
        <v-card-text class="px-4 asid__waiting">
          <p class="asid__state t-title">{{ t('models.subscription.waiting') }}</p>
          <p class="asid__hint t-meta-read">{{ t('models.subscription.codeHint') }}</p>
          <p class="asid__code" data-testid="user-code">{{ flow.user_code }}</p>
          <div class="asid__codetools">
            <v-btn variant="outlined" size="small" @click="copyCode">
              {{ copied ? t('models.subscription.copied') : t('models.subscription.copy') }}
            </v-btn>
            <a class="asid__openlink" :href="flow.verification_uri" target="_blank" rel="noopener">
              {{ t('models.subscription.openPage') }}
            </a>
          </div>
          <p class="asid__countdown t-meta-read t-num">
            {{ t('models.subscription.expiresIn', { minutes: minutesLeft }) }}
          </p>
          <p class="asid__polling t-meta-read">
            <span class="asid__pulse" aria-hidden="true" />
            {{ t('models.subscription.polling') }}
          </p>
        </v-card-text>
        <v-card-actions class="pa-4 pt-0">
          <v-spacer />
          <v-btn variant="text" @click="cancelImport">{{ t('models.subscription.cancelImport') }}</v-btn>
        </v-card-actions>
      </template>

      <!-- success：订阅已连接 + 账号 + 完成。 -->
      <template v-else-if="phase === 'success'">
        <v-card-text class="px-4">
          <p class="asid__state t-title">{{ t('models.subscription.success') }}</p>
          <p v-if="subscription?.account_email" class="asid__account t-meta-read">
            {{ t('models.subscription.accountAs', { email: subscription.account_email }) }}
          </p>
        </v-card-text>
        <v-card-actions class="pa-4 pt-0">
          <v-spacer />
          <v-btn color="primary" @click="finish">{{ t('models.subscription.done') }}</v-btn>
        </v-card-actions>
      </template>

      <!-- expired：代码过期 + 重新开始（回到 start 重新发 flow）。 -->
      <template v-else-if="phase === 'expired'">
        <v-card-text class="px-4">
          <p class="asid__state t-title">{{ t('models.subscription.expired') }}</p>
        </v-card-text>
        <v-card-actions class="pa-4 pt-0">
          <v-spacer />
          <v-btn variant="text" @click="close">{{ t('models.dialog.cancel') }}</v-btn>
          <v-btn color="primary" @click="phase = 'start'">{{ t('models.subscription.restart') }}</v-btn>
        </v-card-actions>
      </template>

      <!-- error：服务端原话 + 重试。框不关。 -->
      <template v-else>
        <v-card-text class="px-4">
          <p class="asid__state t-title">{{ t('models.subscription.failed') }}</p>
          <v-alert type="error" density="compact" variant="tonal" role="alert">{{ errorText }}</v-alert>
        </v-card-text>
        <v-card-actions class="pa-4 pt-0">
          <v-spacer />
          <v-btn variant="text" @click="close">{{ t('models.dialog.cancel') }}</v-btn>
          <v-btn color="primary" :loading="starting" @click="retryWhat === 'start' ? begin() : retry()">
            {{ t('models.subscription.retry') }}
          </v-btn>
        </v-card-actions>
      </template>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.asid__title {
  font-size: 15px;
  line-height: var(--lh-15);
}

.asid__intro {
  margin: 0 0 12px;
  color: var(--text);
  font-size: 13px;
  line-height: var(--lh-13);
}

.asid__waiting {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  text-align: center;
}

.asid__state {
  margin: 0;
  color: var(--ink);
}

.asid__hint {
  margin: 0;
  color: var(--muted);
}

/* 代码是这一块的主语：23 这一档字号 + 等宽，人照着敲不出错。 */
.asid__code {
  margin: 8px 0 0;
  color: var(--ink);
  font-family: var(--font-mono);
  font-size: 23px;
  letter-spacing: 2px;
}

.asid__codetools {
  display: flex;
  align-items: center;
  gap: 12px;
}

.asid__openlink {
  color: var(--accent-ink);
  font-size: 13px;
  line-height: var(--lh-13);
  text-decoration: none;
}

@media (hover: hover) and (pointer: fine) {
  .asid__openlink:hover {
    text-decoration: underline;
  }
}

.asid__countdown {
  margin: 0;
  color: var(--muted);
}

.asid__polling {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  color: var(--muted);
}

/* 轮询脉冲点：只是「还在等」的点缀，文字已经说了同一句话 —— reduced-motion
   关掉它，信息一个字都不少。 */
.asid__pulse {
  width: 8px;
  height: 8px;
  background: var(--muted);
  border-radius: var(--radius-pill);
  animation: asid-pulse 1.6s ease-in-out infinite;
}

@keyframes asid-pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.25;
  }
}

@media (prefers-reduced-motion: reduce) {
  .asid__pulse {
    animation: none;
  }
}

.asid__account {
  margin: 4px 0 0;
  color: var(--muted);
}
</style>
