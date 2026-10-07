<script setup lang="ts">
import type { McpDeclaringType, McpServer } from '../api'

import { onMounted, watch } from 'vue'

import { useProjectMcp } from '@/composables/useProjectMcp'
import { holdRevealGate } from '@/composables/useRevealGate'

import BaseButton from '@/components/base/BaseButton.vue'
import UserRef from '@/components/common/UserRefLink.vue'
import { t } from '@/i18n'
import { relTime } from '@/lib/relTime'

// 项目的远程 MCP 服务器（#1909）。清单、连接状态和每一行那几个变量的值都在
// `useProjectMcp` 里，包括连接走授权服务器网页之后从地址栏带回来的那一次结果
// （mcp_result / mcp_error）。组件只画。
const props = defineProps<{ projectId: string }>()

const { list, error, busy, values, load, connect, disconnect, save, clear, takeCallbackResult } = useProjectMcp(
  () => props.projectId
)

function missing(server: McpServer) {
  return server.variables.filter((v) => !v.set).map((v) => v.name)
}

function statusLine(server: McpServer) {
  switch (server.status) {
    case 'connected':
      // 带授权人的那一句在模板里用 <i18n-t> 画，名字是一颗 UserRef。
      return t('work.mcp.status.connected')
    case 'ready':
      return t('work.mcp.status.ready')
    case 'needs_reconnect':
      return t('work.mcp.status.needsReconnect')
    case 'missing_values':
      return t('work.mcp.status.missingValues', { names: missing(server).join(t('work.mcp.listSeparator')) })
    default:
      return t('work.mcp.status.disconnected')
  }
}

// 它从哪来：项目的 .mcp.json（模板里画，文件名是一段 code），或声明它的那几个
// 队友类型（名字取类型的标题）。
function declaredBy(types: McpDeclaringType[]) {
  const titles = types.map((type) => type.title || type.name).join(t('work.mcp.listSeparator'))
  return t('work.mcp.source.types', { types: titles }, types.length)
}

const DOT: Record<McpServer['status'], string> = {
  connected: 'status-dot--ok',
  ready: 'status-dot--ok',
  needs_reconnect: 'status-dot--warn',
  missing_values: 'status-dot--warn',
  disconnected: 'status-dot--muted',
}

const releaseGate = holdRevealGate()
onMounted(() => {
  takeCallbackResult()
  void load().finally(releaseGate)
})
watch(() => props.projectId, load)
</script>

<template>
  <section id="mcp" class="page-section" data-testid="mcp-servers">
    <div class="page-section-head">
      <v-icon size="14" class="c-faint">mdi-connection</v-icon>
      <span class="page-section-title">{{ t('work.mcp.title') }}</span>
    </div>
    <div class="page-section-body">
      <p class="t-body c-faint settings-hint mb-3">{{ t('work.mcp.hint') }}</p>
      <v-alert v-if="error" type="error" variant="tonal" density="comfortable" class="mb-3">{{ error }}</v-alert>
      <template v-if="list">
        <p v-if="list.problem === 'invalid'" class="t-body c-muted">{{ t('work.mcp.problem.invalid') }}</p>
        <p v-else-if="list.problem === 'unreadable'" class="t-body c-muted">{{ t('work.mcp.problem.unreadable') }}</p>
        <p v-else-if="!list.servers.length" class="t-body c-muted">{{ t('work.mcp.empty') }}</p>
        <ul v-else class="mcp-list">
          <li v-for="server in list.servers" :key="server.name" class="mcp-row" :data-server="server.name">
            <div class="mcp-row__head">
              <div class="mcp-row__name">
                <span class="t-body c-ink mcp-row__title">{{ server.name }}</span>
                <span class="t-meta">{{ server.host }}</span>
                <i18n-t
                  v-if="!server.declared_by"
                  keypath="work.mcp.source.project"
                  tag="span"
                  class="t-meta-read"
                  data-testid="mcp-source"
                >
                  <template #file><code class="mcp-file">.mcp.json</code></template>
                </i18n-t>
                <span v-else class="t-meta-read" data-testid="mcp-source">{{ declaredBy(server.declared_by) }}</span>
              </div>
              <div class="mcp-row__state">
                <span class="status-dot" :class="DOT[server.status]" />
                <i18n-t
                  v-if="server.status === 'connected' && server.authorized_by"
                  keypath="work.mcp.status.connectedBy"
                  tag="span"
                  class="t-body c-text"
                >
                  <template #name><UserRef :handle="server.authorized_by" /></template>
                  <template #when>{{ relTime(server.authorized_at) }}</template>
                </i18n-t>
                <span v-else class="t-body c-text">{{ statusLine(server) }}</span>
              </div>
              <div v-if="server.auth === 'oauth'" class="mcp-row__action">
                <BaseButton
                  v-if="server.status === 'connected'"
                  kind="ghost"
                  size="sm"
                  :loading="busy === server.name"
                  @click="disconnect(server)"
                >
                  {{ t('work.mcp.action.disconnect') }}
                </BaseButton>
                <BaseButton
                  v-else-if="server.status !== 'missing_values'"
                  kind="primary"
                  size="sm"
                  :loading="busy === server.name"
                  @click="connect(server)"
                >
                  {{ t(server.status === 'needs_reconnect' ? 'work.mcp.action.reconnect' : 'work.mcp.action.connect') }}
                </BaseButton>
              </div>
            </div>
            <div v-for="variable in server.variables" :key="variable.name" class="mcp-var">
              <span class="t-meta mcp-var__name">{{ variable.name }}</span>
              <v-text-field
                v-model="values[variable.name]"
                type="password"
                autocomplete="off"
                density="compact"
                variant="outlined"
                hide-details
                class="mcp-var__input"
                :aria-label="variable.name"
                :placeholder="variable.set ? t('work.mcp.valueSet') : ''"
                @keydown.enter="save(variable.name)"
              />
              <BaseButton
                kind="secondary"
                size="sm"
                :disabled="!values[variable.name]?.trim()"
                :loading="busy === variable.name"
                @click="save(variable.name)"
              >
                {{ t('work.mcp.action.save') }}
              </BaseButton>
              <BaseButton v-if="variable.set" kind="ghost" size="sm" @click="clear(variable.name)">
                {{ t('work.mcp.action.clear') }}
              </BaseButton>
              <span v-if="variable.set && variable.updated_by" class="t-meta">
                <i18n-t keypath="work.mcp.valueSetBy" tag="span">
                  <template #name><UserRef :handle="variable.updated_by" /></template>
                  <template #when>{{ relTime(variable.updated_at) }}</template>
                </i18n-t>
              </span>
            </div>
          </li>
        </ul>
      </template>
    </div>
  </section>
</template>

<style scoped>
.mcp-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.mcp-row {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 0;
  border-top: 1px solid var(--line);
}

.mcp-row:first-child {
  border-top: none;
}

.mcp-row__head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px 16px;
}

.mcp-row__name {
  display: flex;
  flex-direction: column;
  min-width: 160px;
}

.mcp-row__title {
  font-weight: 500;
}

.mcp-file {
  font-family: var(--font-mono);
}

.mcp-row__state {
  display: flex;
  align-items: center;
  flex: 1 1 200px;
  gap: 8px;
  min-width: 0;
}

.mcp-var {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.mcp-var__name {
  min-width: 160px;
}

.mcp-var__input {
  flex: 0 1 280px;
}
</style>
