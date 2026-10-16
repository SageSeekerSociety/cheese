<script setup lang="ts">
// 「添加 MCP 服务器」/「编辑」那一张表（#3830 之后的 MCP 设置）。只吃 props、只往上发
// 事件：存在哪、存没存成都是设置页那一层的事。
//
// 只收远程服务器：本地命令那种要在芝士的机器上起进程，仍写在仓库的 .mcp.json 里。
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

const name = ref('')
const url = ref('')
const auth = ref<'oauth' | 'headers'>('oauth')
const header = ref('Authorization')
const value = ref('')

watch(open, (v) => {
  if (!v) return
  name.value = props.initial?.name ?? ''
  url.value = props.initial?.url ?? ''
  auth.value = props.initial?.auth ?? 'oauth'
  header.value = props.initial?.header ?? 'Authorization'
  value.value = ''
})

const NAME = /^[a-z0-9][a-z0-9-]*$/
const nameOk = computed(() => NAME.test(name.value.trim()))
const urlOk = computed(() => /^https:\/\/\S+$/.test(url.value.trim()))
const urlChanged = computed(() => !!props.initial && props.initial.url !== url.value.trim())
const valid = computed(
  () =>
    nameOk.value &&
    urlOk.value &&
    (auth.value === 'oauth' || (!!header.value.trim() && (!!value.value || (!!props.initial && !urlChanged.value))))
)

function submit() {
  if (!valid.value) return
  emit('save', {
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
          :placeholder="initial?.auth === 'headers' && !urlChanged ? t('work.mcp.valueSet') : ''"
          type="password"
          variant="outlined"
          density="comfortable"
          autocomplete="off"
          hide-details
        />
      </div>

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
</style>
