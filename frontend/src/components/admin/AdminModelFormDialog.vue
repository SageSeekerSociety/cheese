<script lang="ts">
/** 表单 emit 出去的那一条，字段与服务端的两个请求体一一对应。
 *
 *  `add` 模式**必带 `name`** —— 它是新模型的身份，服务端也要求；`edit` 模式不带：
 *  `name` 是身份、不是要改的东西，PATCH 的语义是「只发要动的那些」，把身份也塞回去
 *  只会让网关收到一个它不认的字段。两个模式就差这一处，所以合成一个 `name` 可选的
 *  形状，而不是拆成两份。页面拿它去调 `createGatewayModel` / `updateGatewayModel`，
 *  类型因此对得上（早先这里是 `Record<string, unknown>`，两条路都塞不进入参）。
 *
 *  单价与能力的形状就地写开、不去 `import @/api` 的类型：这个块排在 `<script setup>`
 *  之前，从这里引一个 `@/` 开头的依赖会让 `simple-import-sort` 要求的顺序和文件里的
 *  物理顺序对不上。结构上与 `GatewayPrices` / `GatewayCapabilities` 一致，赋给
 *  `createGatewayModel` 的入参过得去。 */
export interface ModelFormPayload {
  name?: string
  upstream_model: string
  api_base?: string
  api_key?: string
  api_key_unchanged?: boolean
  label: string
  selectable: boolean
  prices: {
    input: number | null
    output: number | null
    cache_read: number | null
    cache_creation: number | null
  }
  capabilities: {
    reasoning: boolean
    vision: boolean
    adaptive_thinking: boolean
  }
}
</script>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

// 新增 / 编辑一个**运行时**模型的表单（契约 §3.3）。
//
// 这一层只做三件事：收字、按服务端的同一套规则先自我校验、把结果 emit 出去。真正
// 打接口的是页面 —— 因为「失败了表单不关」这条要求需要一个地方同时看到**请求结果**和
// **表单是否还开着**，而那两样都归页面（这一层拿不到请求的成败）。
//
// 三条校验与服务端逐字同源，写在界面上是**为了省一次往返**，不是第二份判据：
//
//   * `name` 1..64、只含 `[A-Za-z0-9._-]`；
//   * `api_base` 要么空，要么 `https://` 开头（空 = 用上游默认端点）；
//   * `selectable`（上架）要求**输入与输出两个单价都 > 0** —— 无价模型会让项目的
//     max_budget 这道刹车静默失效，所以这条闸门在界面上就把它拦住（开关直接灰掉），
//     而不是等服务端 400。
//
// 单价在这一层按「每百万 token 的美元」收，提交时换算回网关的「每 token」。
// 换算只在这里做一次：网关账本和价目表是两种量纲，让用户对着 4.2e-7 填数字是不现实的，
// 而价格单元格（`AdminModelPriceCell`）显示的是同一个「每百万」口径 —— 两处一致。

/** 编辑时要预填的那一行（§3.1 的模型项里这一层用得到的字段）。 */
interface ModelSeed {
  name: string
  label: string
  selectable: boolean
  upstream: { model: string }
  prices: {
    input?: number | null
    output?: number | null
    cache_read?: number | null
    cache_creation?: number | null
  }
  capabilities: { reasoning?: boolean; vision?: boolean; adaptive_thinking?: boolean }
}

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    /** `add` 收新模型的所有字段；`edit` 的 `name` 是身份，不可改。 */
    mode: 'add' | 'edit'
    saving?: boolean
    /** 上一次提交失败的**服务端原话**。非空时页面把它显示在框里，且框不关。 */
    error?: string | null
    /** `edit` 的当前值。 */
    seed?: ModelSeed | null
  }>(),
  { saving: false, error: null, seed: null }
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'submit', payload: ModelFormPayload): void
}>()

const { t } = useI18n()

const NAME_RE = /^[A-Za-z0-9._-]{1,64}$/

// 表单状态。单价用字符串收（`v-text-field` 给的就是字符串），提交时才解析 ——
// 用 number 收的话空框会变成 0，而 0 是「确实免费」，和「没填」是两回事。
const name = ref('')
const label = ref('')
const upstreamModel = ref('')
const apiBase = ref('')
const apiKey = ref('')
const selectable = ref(false)
const priceInput = ref('')
const priceOutput = ref('')
const priceCacheRead = ref('')
const priceCacheCreation = ref('')
const reasoning = ref(false)
const vision = ref(false)
const adaptiveThinking = ref(false)

/** 每百万 → 每 token，空串给 null。 */
function toPerToken(text: string): number | null {
  const v = text.trim()
  if (v === '') return null
  const n = Number(v)
  if (!Number.isFinite(n) || n < 0) return null
  return n / 1_000_000
}

/** 每 token → 每百万的输入框文本（编辑预填用）。显示成 8 位小数去掉尾零。 */
function toPerMillionText(v: number | null | undefined): string {
  if (v === null || v === undefined) return ''
  const scaled = Number((v * 1_000_000).toFixed(8))
  return scaled === 0 ? '' : String(scaled)
}

/** 对话框每次**打开**都从 seed 重填一遍：上一次留下的半截输入不该跟着下一次打开回来，
 *  而编辑的预填值又必须是最新的那一行。 */
watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    const s = props.seed
    name.value = props.mode === 'edit' ? s?.name ?? '' : ''
    label.value = s?.label ?? ''
    upstreamModel.value = s?.upstream.model ?? ''
    apiBase.value = ''
    apiKey.value = ''
    selectable.value = s?.selectable ?? false
    priceInput.value = toPerMillionText(s?.prices.input)
    priceOutput.value = toPerMillionText(s?.prices.output)
    priceCacheRead.value = toPerMillionText(s?.prices.cache_read)
    priceCacheCreation.value = toPerMillionText(s?.prices.cache_creation)
    reasoning.value = s?.capabilities.reasoning ?? false
    vision.value = s?.capabilities.vision ?? false
    adaptiveThinking.value = s?.capabilities.adaptive_thinking ?? false
  },
  { immediate: true }
)

const inputNum = computed(() => toPerToken(priceInput.value) ?? 0)
const outputNum = computed(() => toPerToken(priceOutput.value) ?? 0)

/** 双向都有价才允许上架。这是「无价模型不能上架」那条不变式的界面侧。 */
const canSelect = computed(() => inputNum.value > 0 && outputNum.value > 0)

// 关了上架再清价没问题，开着上架把价清空则必须跟着关 —— 不关的话界面会停在一个
// 「勾着上架、却发不出合法请求」的状态，而服务端会 400。
watch(canSelect, (ok) => {
  if (!ok) selectable.value = false
})

const nameOk = computed(() => NAME_RE.test(name.value.trim()))
const upstreamOk = computed(() => upstreamModel.value.trim() !== '')
const apiBaseOk = computed(() => {
  const v = apiBase.value.trim()
  return v === '' || v.startsWith('https://')
})

const valid = computed(() => upstreamOk.value && apiBaseOk.value && (props.mode === 'edit' || nameOk.value))

function close() {
  emit('update:modelValue', false)
}

function submit() {
  if (!valid.value || props.saving) return
  const payload: ModelFormPayload = {
    upstream_model: upstreamModel.value.trim(),
    label: label.value.trim(),
    selectable: selectable.value,
    prices: {
      input: toPerToken(priceInput.value),
      output: toPerToken(priceOutput.value),
      cache_read: toPerToken(priceCacheRead.value),
      cache_creation: toPerToken(priceCacheCreation.value),
    },
    capabilities: {
      reasoning: reasoning.value,
      vision: vision.value,
      adaptive_thinking: adaptiveThinking.value,
    },
  }
  if (props.mode === 'add') payload.name = name.value.trim()
  // api_base / api_key 只在**填了**的时候发出去：编辑时留空表示「不动」
  // （契约 §3.3：`api_key` 为空且 `api_key_unchanged=true` ⇒ 不改上游凭据）。
  // 这条「不改动」只对 PATCH 有意义 —— 新增本来就没有已存凭据可留，所以 add 不发它。
  if (apiBase.value.trim()) payload.api_base = apiBase.value.trim()
  if (apiKey.value.trim()) payload.api_key = apiKey.value.trim()
  else if (props.mode === 'edit') payload.api_key_unchanged = true
  emit('submit', payload)
}
</script>

<template>
  <v-dialog :model-value="modelValue" max-width="560" :persistent="saving" @update:model-value="!$event && close()">
    <v-card rounded="lg">
      <v-card-title class="px-4 pt-4 pb-2">
        {{ mode === 'add' ? t('models.dialog.add.title') : t('models.dialog.edit.title') }}
      </v-card-title>

      <v-card-text class="px-4">
        <div class="amf__fields">
          <v-text-field
            v-model="name"
            autocomplete="off"
            variant="outlined"
            density="comfortable"
            :label="t('models.dialog.field.name')"
            :hint="t('models.dialog.field.nameHint')"
            :disabled="mode === 'edit'"
            :error="mode === 'add' && name !== '' && !nameOk"
            hide-details="auto"
          />
          <v-text-field
            v-model="label"
            autocomplete="off"
            variant="outlined"
            density="comfortable"
            :label="t('models.dialog.field.label')"
            hide-details="auto"
          />
          <v-text-field
            v-model="upstreamModel"
            autocomplete="off"
            variant="outlined"
            density="comfortable"
            :label="t('models.dialog.field.upstreamModel')"
            :hint="t('models.dialog.field.upstreamModelHint')"
            hide-details="auto"
          />
          <v-text-field
            v-model="apiBase"
            autocomplete="off"
            variant="outlined"
            density="comfortable"
            :label="t('models.dialog.field.apiBase')"
            :hint="t('models.dialog.field.apiBaseHint')"
            :error="!apiBaseOk"
            hide-details="auto"
          />
          <v-text-field
            v-model="apiKey"
            autocomplete="off"
            type="password"
            variant="outlined"
            density="comfortable"
            :label="t('models.dialog.field.apiKey')"
            :hint="mode === 'edit' ? t('models.dialog.field.apiKeyKeep') : t('models.dialog.field.apiKeyHint')"
            hide-details="auto"
          />

          <!-- 上架开关。没价时**灰掉**，并说明为什么 —— 一个能打开却发不出去的开关
               比一个灰掉的开关更容易让人在服务端 400 之后才发现原因。 -->
          <div class="amf__switch">
            <v-switch
              v-model="selectable"
              color="primary"
              density="compact"
              :label="t('models.dialog.field.selectable')"
              :disabled="!canSelect"
              hide-details
            />
            <p v-if="!canSelect" class="amf__hint t-meta-read">
              {{ t('models.dialog.selectableNeedsPrice') }}
            </p>
          </div>

          <p class="amf__group t-eyebrow-read">{{ t('models.dialog.group.prices') }}</p>
          <p class="amf__hint t-meta-read">{{ t('models.dialog.priceUnit') }}</p>
          <div class="amf__prices">
            <v-text-field
              v-model="priceInput"
              autocomplete="off"
              type="number"
              variant="outlined"
              density="comfortable"
              :label="t('models.price.input')"
              hide-details
            />
            <v-text-field
              v-model="priceOutput"
              autocomplete="off"
              type="number"
              variant="outlined"
              density="comfortable"
              :label="t('models.price.output')"
              hide-details
            />
            <v-text-field
              v-model="priceCacheRead"
              autocomplete="off"
              type="number"
              variant="outlined"
              density="comfortable"
              :label="t('models.price.cacheRead')"
              hide-details
            />
            <v-text-field
              v-model="priceCacheCreation"
              autocomplete="off"
              type="number"
              variant="outlined"
              density="comfortable"
              :label="t('models.price.cacheCreation')"
              hide-details
            />
          </div>

          <p class="amf__group t-eyebrow-read">{{ t('models.dialog.group.capabilities') }}</p>
          <div class="amf__caps">
            <v-switch
              v-model="reasoning"
              color="primary"
              density="compact"
              :label="t('models.capability.reasoning')"
              hide-details
            />
            <v-switch
              v-model="vision"
              color="primary"
              density="compact"
              :label="t('models.capability.vision')"
              hide-details
            />
            <v-switch
              v-model="adaptiveThinking"
              color="primary"
              density="compact"
              :label="t('models.capability.adaptiveThinking')"
              hide-details
            />
          </div>
        </div>

        <v-alert v-if="error" type="error" density="compact" variant="tonal" class="amf__alert" role="alert">
          {{ error }}
        </v-alert>
      </v-card-text>

      <v-card-actions class="pa-4 pt-0">
        <v-spacer />
        <v-btn variant="text" :disabled="saving" @click="close">{{ t('models.dialog.cancel') }}</v-btn>
        <v-btn color="primary" :loading="saving" :disabled="!valid || saving" @click="submit">
          {{ mode === 'add' ? t('models.dialog.create') : t('models.dialog.save') }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
/* 字段之间的缝由这里给：`hide-details`（含 `auto` 在没有 hint 时）会收掉 `.v-input__details`
   那一行，而 outlined 字段的浮动标签有一半悬在框的上沿之上（`.claude/rules/frontend.md`）。
   不自己给这个 gap，下一条字段的标签就会压在上一条的下边框上。 */
.amf__fields {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.amf__switch {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.amf__group {
  margin-top: 8px;
  color: var(--muted);
}

.amf__hint {
  margin: 0;
  color: var(--muted);
}

.amf__prices {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.amf__caps {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
}

.amf__alert {
  margin-top: 12px;
}
</style>
