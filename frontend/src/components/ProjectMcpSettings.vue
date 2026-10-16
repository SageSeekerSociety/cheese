<script setup lang="ts">
// 项目的远程 MCP 服务器（#1909）。清单来自三处：在这里添加的、仓库默认分支的
// .mcp.json、AI 队友类型自带的。连接属于项目，项目里所有人通过 AI 队友共用一个账号，
// 谁授权的写在每一行上。在这里添加的那几条可以编辑、删除，别的来源要去它们自己那里改。
//
// 只吃 props、只往上发事件（`frontend_grade.py` 的 A 级）：取数、连接/断开/填密钥、
// 以及授权回来那一条回跳都在 `composables/useProjectMcpSettings.ts` 里，由设置页接线。
// 密钥输入框里那几行的草稿也在那一层（成了要把那一行清空），这里按 variable 的名字
// 报上去、画回去。
import type { McpDeclaringType, McpServer, McpServerList } from '../api'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import UserRef from '@/components/common/UserRefLink.vue'
import { t } from '@/i18n'
import { relTime } from '@/lib/relTime'

const props = defineProps<{
  /** 读到的服务器清单；还没读到是 null。 */
  list: McpServerList | null
  /** 读失败：那一句原话。 */
  error: string
  /** 一次动作还在路上：那一个服务器或变量的名字（'' = 没有）。 */
  busy: string
  /** 每个变量正在填的密钥原文（按 variable 的名字）。
   *  `props` 里读得到，改要走 `update:value` —— 那一层才管清空。 */
  values: Record<string, string>
  /** 能不能添加、编辑、删除、连接（配置 AI 队友的权限）。 */
  manages: boolean
  agentName: string
}>()

const emit = defineEmits<{
  (e: 'connect', server: McpServer): void
  (e: 'disconnect', server: McpServer): void
  (e: 'save', name: string): void
  (e: 'clear', name: string): void
  (e: 'update:value', value: { name: string; value: string }): void
  (e: 'add'): void
  (e: 'edit', server: McpServer): void
  (e: 'remove', server: McpServer): void
}>()

function missing(server: McpServer) {
  return server.variables.filter((v) => !v.set).map((v) => v.name)
}

function statusLine(server: McpServer) {
  if (server.shadowed) return t('work.mcp.status.shadowed')
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

function dot(server: McpServer) {
  return server.shadowed ? 'status-dot--warn' : DOT[server.status]
}

const DOT: Record<McpServer['status'], string> = {
  connected: 'status-dot--ok',
  ready: 'status-dot--ok',
  needs_reconnect: 'status-dot--warn',
  missing_values: 'status-dot--warn',
  disconnected: 'status-dot--muted',
}

// 密钥输入框里那一行画的字：那一层给的原文（没填就是空）。`values` 是 props，所以走
// 一个局部读口，省得模板里到处判空。
const fieldValue = (name: string) => props.values[name] ?? ''
</script>

<template>
  <section id="mcp" class="page-section" data-testid="mcp-servers">
    <div class="page-section-head">
      <v-icon size="14" class="c-faint">mdi-connection</v-icon>
      <span class="page-section-title">{{ t('work.mcp.title') }}</span>
      <BaseButton v-if="manages" kind="secondary" size="sm" prepend-icon="mdi-plus" class="ms-auto" @click="emit('add')">
        {{ t('work.mcp.action.add') }}
      </BaseButton>
    </div>
    <div class="page-section-body">
      <p class="t-body c-muted settings-hint mb-3">{{ t('work.mcp.hint', { agent: agentName }) }}</p>
      <v-alert v-if="error" type="error" variant="tonal" density="comfortable" class="mb-3">{{ error }}</v-alert>
      <template v-if="list">
        <p v-if="list.problem === 'invalid'" class="t-body c-muted">{{ t('work.mcp.problem.invalid') }}</p>
        <p v-else-if="list.problem === 'unreadable'" class="t-body c-muted">{{ t('work.mcp.problem.unreadable') }}</p>
        <BaseEmptyState v-else-if="!list.servers.length" size="inline" :title="t('work.mcp.empty')" />
        <ul v-else class="mcp-list">
          <li v-for="server in list.servers" :key="server.name" class="mcp-row" :data-server="server.name">
            <div class="mcp-row__head">
              <div class="mcp-row__name">
                <span class="t-body c-ink mcp-row__title">{{ server.name }}</span>
                <span class="t-meta">{{ server.host }}</span>
                <i18n-t
                  v-if="server.added"
                  scope="global"
                  keypath="work.mcp.source.settings"
                  tag="span"
                  class="t-meta-read"
                  data-testid="mcp-source"
                >
                  <template #name><UserRef :handle="server.added.by" /></template>
                </i18n-t>
                <i18n-t
                  v-else-if="!server.declared_by"
                  scope="global"
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
                <span class="status-dot" :class="dot(server)" />
                <i18n-t
                  v-if="!server.shadowed && server.status === 'connected' && server.authorized_by"
                  scope="global"
                  keypath="work.mcp.status.connectedBy"
                  tag="span"
                  class="t-body c-text"
                >
                  <template #name><UserRef :handle="server.authorized_by" /></template>
                  <template #when>{{ relTime(server.authorized_at) }}</template>
                </i18n-t>
                <span v-else class="t-body c-text">{{ statusLine(server) }}</span>
              </div>
              <div class="mcp-row__action">
                <template v-if="manages && !server.shadowed && server.auth === 'oauth'">
                <BaseButton
                  v-if="server.status === 'connected'"
                  kind="ghost"
                  size="sm"
                  :loading="busy === server.name"
                  @click="emit('disconnect', server)"
                >
                  {{ t('work.mcp.action.disconnect') }}
                </BaseButton>
                <BaseButton
                  v-else-if="server.status !== 'missing_values'"
                  kind="primary"
                  size="sm"
                  :loading="busy === server.name"
                  @click="emit('connect', server)"
                >
                  {{ t(server.status === 'needs_reconnect' ? 'work.mcp.action.reconnect' : 'work.mcp.action.connect') }}
                </BaseButton>
                </template>
                <template v-if="manages && server.added">
                  <BaseButton kind="ghost" size="sm" @click="emit('edit', server)">
                    {{ t('work.mcp.action.edit') }}
                  </BaseButton>
                  <BaseButton kind="ghost" size="sm" @click="emit('remove', server)">
                    {{ t('work.mcp.action.remove') }}
                  </BaseButton>
                </template>
              </div>
            </div>
            <div v-for="variable in server.added ? [] : server.variables" :key="variable.name" class="mcp-var">
              <span class="t-meta mcp-var__name">{{ variable.name }}</span>
              <v-text-field
                :model-value="fieldValue(variable.name)"
                type="password"
                autocomplete="off"
                density="compact"
                variant="outlined"
                hide-details
                class="mcp-var__input"
                :aria-label="variable.name"
                :placeholder="variable.set ? t('work.mcp.valueSet') : ''"
                @update:model-value="(value) => emit('update:value', { name: variable.name, value })"
                @keydown.enter="emit('save', variable.name)"
              />
              <BaseButton
                kind="secondary"
                size="sm"
                :disabled="!fieldValue(variable.name).trim()"
                :loading="busy === variable.name"
                @click="emit('save', variable.name)"
              >
                {{ t('work.mcp.action.save') }}
              </BaseButton>
              <BaseButton v-if="variable.set" kind="ghost" size="sm" @click="emit('clear', variable.name)">
                {{ t('work.mcp.action.clear') }}
              </BaseButton>
              <span v-if="variable.set && variable.updated_by" class="t-meta">
                <i18n-t scope="global" keypath="work.mcp.valueSetBy" tag="span">
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

.mcp-row__action {
  display: flex;
  align-items: center;
  gap: 4px;
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
