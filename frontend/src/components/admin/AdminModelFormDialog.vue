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
  label: string
  selectable: boolean
  capabilities: {
    reasoning: boolean
    vision: boolean
    adaptive_thinking: boolean
    mid_conversation_system?: boolean
  }
  /** 只在 `add` 出现：新模型的第一条线路（结构上与 `GatewaySourceFields` 一致）。
   *  编辑模型不碰线路 —— 一个模型可能有好几条，改哪条得在线路上改。 */
  source?: {
    protocol?: 'anthropic' | 'openai-chat' | 'openai-responses' | null
    upstream_model?: string
    api_base?: string | null
    api_key?: string | null
    api_key_unchanged?: boolean
    prices?: {
      input?: number | null
      output?: number | null
      cache_read?: number | null
      cache_creation?: number | null
    }
    order?: number
    free?: boolean
    upstream_headers?: Record<string, string>
    session_header?: string
  }
}
</script>

<script setup lang="ts">
import type { SourceDraft } from '@/lib/adminSources'

import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminSourceFields from '@/components/admin/models/AdminSourceFields.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import { draftPriced, draftToFields, draftValid, emptySourceDraft } from '@/lib/adminSources'

// 新增 / 编辑一个**运行时**模型的表单（契约 §3.3）。
//
// 这一层只做三件事：收字、按服务端的同一套规则先自我校验、把结果 emit 出去。真正
// 打接口的是页面 —— 因为「失败了表单不关」这条要求需要一个地方同时看到**请求结果**和
// **表单是否还开着**，而那两样都归页面（这一层拿不到请求的成败）。
//
// 新增时收两样：模型自己的（名字、标签、上架、能力）和它的第一条线路（`AdminSourceFields`，
// 与「添加线路」那个框同一组字段）。编辑只改模型自己的：一个模型可能有好几条线路，上游、
// 地址、凭据、单价都在线路上改（详情抽屉的「线路」一段）。
//
// 校验与服务端同源，写在界面上是**为了省一次往返**，不是第二份判据：
//
//   * `name` 1..64、只含 `[A-Za-z0-9._-]`；
//   * 线路那组字段的规则在 `lib/adminSources.ts`；
//   * `selectable`（上架）要求每条线路都有价（输入与输出都 > 0，或标了免费）—— 无价模型
//     会让项目的 max_budget 这道刹车静默失效，所以开关在界面上就灰掉，而不是等服务端 400。

/** 编辑时要预填的那一行（§3.1 的模型项里这一层用得到的字段）。 */
interface ModelSeed {
  name: string
  label: string
  selectable: boolean
  /** 每条在用的线路都有价（或标了免费）。编辑时上架开关的闸门。 */
  priced: boolean
  capabilities: {
    reasoning?: boolean
    vision?: boolean
    adaptive_thinking?: boolean
    mid_conversation_system?: boolean
  }
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

const name = ref('')
const label = ref('')
const selectable = ref(false)
const source = ref<SourceDraft>(emptySourceDraft())
const reasoning = ref(false)
const vision = ref(false)
const adaptiveThinking = ref(false)
// 能否收 Claude Code 插在对话中间的 system 消息。没标过就是照常发，开关默认开；
// 只有标过、或这次关掉，才写回网关。
const midConversationSystem = ref(true)
const midConversationSystemMarked = ref(false)

/** 对话框每次**打开**都从 seed 重填一遍：上一次留下的半截输入不该跟着下一次打开回来，
 *  而编辑的预填值又必须是最新的那一行。 */
watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    const s = props.seed
    name.value = props.mode === 'edit' ? s?.name ?? '' : ''
    label.value = s?.label ?? ''
    selectable.value = s?.selectable ?? false
    source.value = emptySourceDraft()
    reasoning.value = s?.capabilities.reasoning ?? false
    vision.value = s?.capabilities.vision ?? false
    adaptiveThinking.value = s?.capabilities.adaptive_thinking ?? false
    midConversationSystem.value = s?.capabilities.mid_conversation_system ?? true
    midConversationSystemMarked.value = s?.capabilities.mid_conversation_system !== undefined
  },
  { immediate: true }
)

/** 上架的闸门：新增看第一条线路有没有价，编辑看服务端说的整组有没有价。 */
const canSelect = computed(() => (props.mode === 'add' ? draftPriced(source.value) : !!props.seed?.priced))

// 关了上架再清价没问题，开着上架把价清空则必须跟着关 —— 不关的话界面会停在一个
// 「勾着上架、却发不出合法请求」的状态，而服务端会 400。
watch(canSelect, (ok) => {
  if (!ok) selectable.value = false
})

// Chat 线路翻成 chat/completions 会丢掉对话中间的 system 消息：服务端会把这一项标成关，
// 界面先跟着关上，免得保存后看到的和填的不一样。
watch(
  () => source.value.protocol,
  (protocol) => {
    if (protocol !== 'openai-chat') return
    midConversationSystem.value = false
    midConversationSystemMarked.value = true
  }
)

const nameOk = computed(() => NAME_RE.test(name.value.trim()))

const valid = computed(() => props.mode === 'edit' || (nameOk.value && draftValid(source.value)))

function close() {
  emit('update:modelValue', false)
}

function submit() {
  if (!valid.value || props.saving) return
  const payload: ModelFormPayload = {
    label: label.value.trim(),
    selectable: selectable.value,
    capabilities: {
      reasoning: reasoning.value,
      vision: vision.value,
      adaptive_thinking: adaptiveThinking.value,
    },
  }
  if (midConversationSystemMarked.value || !midConversationSystem.value) {
    payload.capabilities.mid_conversation_system = midConversationSystem.value
  }
  if (props.mode === 'add') {
    payload.name = name.value.trim()
    payload.source = draftToFields(source.value, 'add')
  }
  emit('submit', payload)
}
</script>

<template>
  <AdaptiveDialog
    :model-value="modelValue"
    :title="mode === 'add' ? t('models.dialog.add.title') : t('models.dialog.edit.title')"
    :primary-label="mode === 'add' ? t('models.dialog.create') : t('models.dialog.save')"
    :primary-loading="saving"
    :primary-disabled="!valid || saving"
    :persistent="saving"
    :close-disabled="saving"
    @update:model-value="!$event && close()"
    @primary="submit"
  >
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
      <!-- 第一条线路：只在新增时出现。编辑时线路在详情抽屉里一条条改。 -->
      <template v-if="mode === 'add'">
        <p class="amf__group t-eyebrow-read">{{ t('models.source.firstSource') }}</p>
        <AdminSourceFields v-model="source" />
      </template>

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
        <v-switch
          v-model="midConversationSystem"
          color="primary"
          density="compact"
          :label="t('models.capability.midConversationSystem')"
          hide-details
        />
      </div>
    </div>

    <v-alert v-if="error" type="error" density="compact" variant="tonal" class="amf__alert" role="alert">
      {{ error }}
    </v-alert>
  </AdaptiveDialog>
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

.amf__caps {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
}

.amf__alert {
  margin-top: 12px;
}
</style>
