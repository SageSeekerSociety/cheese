<script setup lang="ts">
// 「添加 MCP 服务器」/「编辑」那一张表（#3830 之后的 MCP 设置）。只吃 props、只往上发
// 事件：存在哪、存没存成都是设置页那一层的事。
//
// 两种：远程服务器（地址和登录方式），或在项目机器上运行的一条命令（命令和环境变量）。
// 命令要的密钥写成 ${VAR}，加好之后在列表那一行填值，不写进命令本身。
import type { McpServerDraft } from '../api'

import { computed, ref, watch } from 'vue'

import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import { t } from '@/i18n'

const props = defineProps<{
  /** 编辑时是那一条的现状；添加时是 null。 */
  initial: McpServerDraft | null
  agentName: string
  saving: boolean
  error: string
}>()
const open = defineModel<boolean>({ required: true })
const emit = defineEmits<{ (e: 'save', draft: McpServerDraft): void }>()

const kind = ref<'remote' | 'command'>('remote')
const name = ref('')
const url = ref('')
const command = ref('')
const env = ref('')
const auth = ref<'oauth' | 'headers'>('oauth')
const header = ref('Authorization')
const value = ref('')

watch(
  open,
  (v) => {
    if (!v) return
    const initial = props.initial
    const remote = initial?.kind === 'remote' ? initial : null
    const local = initial?.kind === 'command' ? initial : null
    kind.value = initial?.kind ?? 'remote'
    name.value = initial?.name ?? ''
    url.value = remote?.url ?? ''
    auth.value = remote?.auth ?? 'oauth'
    header.value = remote?.header ?? 'Authorization'
    value.value = ''
    command.value = local?.command ?? ''
    env.value = Object.entries(local?.env ?? {})
      .map(([key, text]) => `${key}=${text}`)
      .join('\n')
  },
  { immediate: true }
)

const NAME = /^[a-z0-9][a-z0-9-]*$/
const nameOk = computed(() => NAME.test(name.value.trim()))
const urlOk = computed(() => /^https:\/\/\S+$/.test(url.value.trim()))
const urlChanged = computed(() => props.initial?.kind === 'remote' && props.initial.url !== url.value.trim())
const remoteOk = computed(
  () =>
    urlOk.value &&
    (auth.value === 'oauth' || (!!header.value.trim() && (!!value.value || (!!props.initial && !urlChanged.value))))
)

/** 环境变量一行一个 `名称=值`；有一行不是这样写的就是 null。空行不算。 */
const ENV_LINE = /^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/
const envVars = computed<Record<string, string> | null>(() => {
  const out: Record<string, string> = {}
  for (const line of env.value.split('\n')) {
    if (!line.trim()) continue
    const found = ENV_LINE.exec(line.trim())
    if (!found) return null
    out[found[1]] = found[2]
  }
  return out
})
const commandOk = computed(() => !!command.value.trim() && envVars.value !== null)
const valid = computed(() => nameOk.value && (kind.value === 'remote' ? remoteOk.value : commandOk.value))

// 提示里的示例变量：不是中文文案，是要照着写的写法。
const VARIABLE_EXAMPLE = '${API_KEY}'
const ENV_EXAMPLE = 'API_KEY=${API_KEY}'

function submit() {
  if (!valid.value) return
  if (kind.value === 'command') {
    emit('save', { kind: 'command', name: name.value.trim(), command: command.value.trim(), env: envVars.value ?? {} })
    return
  }
  emit('save', {
    kind: 'remote',
    name: name.value.trim(),
    url: url.value.trim(),
    auth: auth.value,
    header: auth.value === 'headers' ? header.value.trim() : null,
    value: auth.value === 'headers' && value.value ? value.value : null,
  })
}
</script>

<template>
  <AdaptiveDialog
    v-model="open"
    :title="initial ? t('work.mcp.form.editTitle', { name: initial.name }) : t('work.mcp.form.addTitle')"
    :primary-label="initial ? t('work.mcp.form.save') : t('work.mcp.form.add')"
    :primary-loading="saving"
    :primary-disabled="!valid"
    :cancel-label="t('work.mcp.form.cancel')"
    :max-width="520"
    @primary="submit"
  >
    <div class="mcp-form">
      <div>
        <div class="t-body c-ink mb-1">{{ t('work.mcp.form.kind') }}</div>
        <v-radio-group v-model="kind" inline hide-details density="compact" :disabled="!!initial">
          <v-radio value="remote" :label="t('work.mcp.group.remote')" />
          <v-radio value="command" :label="t('work.mcp.group.local')" class="ms-4" />
        </v-radio-group>
      </div>
      <v-text-field
        v-model="name"
        :label="t('work.mcp.form.name')"
        :hint="t('work.mcp.form.nameHint', { agent: agentName })"
        persistent-hint
        variant="outlined"
        density="comfortable"
        :disabled="!!initial"
        autocomplete="off"
      />
      <template v-if="kind === 'command'">
        <v-text-field
          v-model="command"
          :label="t('work.mcp.form.command')"
          :hint="t('work.mcp.form.commandHint', { example: VARIABLE_EXAMPLE, agent: agentName })"
          persistent-hint
          placeholder="npx -y @playwright/mcp@latest"
          variant="outlined"
          density="comfortable"
          autocomplete="off"
          spellcheck="false"
          class="mcp-form__mono"
        />
        <v-textarea
          v-model="env"
          :label="t('work.mcp.form.env')"
          :hint="t('work.mcp.form.envHint', { example: ENV_EXAMPLE })"
          :error-messages="envVars === null ? t('work.mcp.form.envInvalid') : undefined"
          persistent-hint
          rows="2"
          auto-grow
          variant="outlined"
          density="comfortable"
          autocomplete="off"
          spellcheck="false"
          class="mcp-form__mono"
        />
        <p class="t-meta c-muted mb-0">{{ t('work.mcp.form.commandOwner') }}</p>
      </template>
      <template v-else>
        <v-text-field
          v-model="url"
          :label="t('work.mcp.form.url')"
          placeholder="https://mcp.example.com/mcp"
          variant="outlined"
          density="comfortable"
          autocomplete="off"
          hide-details="auto"
        />
        <p v-if="urlChanged" class="t-meta c-muted mb-0">{{ t('work.mcp.form.urlChanged') }}</p>

        <div>
          <div class="t-body c-ink mb-1">{{ t('work.mcp.form.auth') }}</div>
          <v-radio-group v-model="auth" hide-details density="compact">
            <v-radio value="oauth">
              <template #label>
                <div>
                  <div class="t-body c-text">{{ t('work.mcp.form.oauth') }}</div>
                  <div class="t-meta c-muted">{{ t('work.mcp.form.oauthHint') }}</div>
                </div>
              </template>
            </v-radio>
            <v-radio value="headers" class="mt-2">
              <template #label>
                <div>
                  <div class="t-body c-text">{{ t('work.mcp.form.headers') }}</div>
                  <div class="t-meta c-muted">{{ t('work.mcp.form.headersHint', { agent: agentName }) }}</div>
                </div>
              </template>
            </v-radio>
          </v-radio-group>
        </div>

        <div v-if="auth === 'headers'" class="mcp-form__header">
          <v-text-field
            v-model="header"
            :label="t('work.mcp.form.headerName')"
            variant="outlined"
            density="comfortable"
            autocomplete="off"
            hide-details
            class="mcp-form__header-name"
          />
          <v-text-field
            v-model="value"
            :label="t('work.mcp.form.headerValue')"
            :placeholder="
              initial?.kind === 'remote' && initial.auth === 'headers' && !urlChanged ? t('work.mcp.valueSet') : ''
            "
            type="password"
            variant="outlined"
            density="comfortable"
            autocomplete="off"
            hide-details
          />
        </div>
      </template>

      <v-alert v-if="error" type="error" variant="tonal" density="comfortable">{{ error }}</v-alert>
    </div>
  </AdaptiveDialog>
</template>

<style scoped>
.mcp-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding-top: 8px;
}

.mcp-form__header {
  display: grid;
  grid-template-columns: 160px 1fr;
  gap: 12px;
}

.mcp-form__header-name {
  min-width: 0;
}

.mcp-form__mono :deep(input),
.mcp-form__mono :deep(textarea) {
  font-family: var(--font-mono);
}
</style>
