<script setup lang="ts">
import type { McpDeclaringType, McpServer, McpServerList } from '../api'

import { onMounted, reactive, ref, watch } from 'vue'
import { toast } from 'vuetify-sonner'

import { useNavigation } from '@/composables/useNavigation'
import { holdRevealGate } from '@/composables/useRevealGate'

import { clearMcpSecret, connectMcpServer, disconnectMcpServer, getMcpServers, setMcpSecret } from '../api'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import UserRef from '@/components/common/UserRefLink.vue'
import { t } from '@/i18n'
import { goAuthorize } from '@/lib/desktopApp'
import { renderNoticeMessage } from '@/lib/noticeText'
import { relTime } from '@/lib/relTime'

// 项目的远程 MCP 服务器（#1909）。清单来自仓库默认分支的 .mcp.json，连接属于项目：
// 任何成员都能连接或断开，谁授权的写在每一行上。连接走授权服务器的网页，回来时
// 落在这一页，结果在地址栏的 mcp_result / mcp_error 里。mcp_error 是 apiError 里
// 那句话的 key（参数在 mcp_error_params，JSON），这里按读者的语言说出来。
const props = defineProps<{ projectId: string }>()

const nav = useNavigation()
const list = ref<McpServerList | null>(null)
const error = ref('')
const busy = ref('')
const values = reactive<Record<string, string>>({})

async function load() {
  error.value = ''
  try {
    list.value = await getMcpServers(props.projectId)
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('work.mcp.loadFailed')
  }
}

// 连接、断开、填密钥这些都是一次性动作：结果跟这一次点击走，用全局 toast 说一声。
function fail(e: unknown, fallback: string) {
  toast.error(e instanceof Error ? e.message : fallback)
}

async function connect(server: McpServer) {
  busy.value = server.name
  try {
    const { authorization_url } = await connectMcpServer(props.projectId, server.name)
    if (goAuthorize(authorization_url)) busy.value = ''
  } catch (e) {
    fail(e, t('work.mcp.connectFailed'))
    busy.value = ''
  }
}

async function disconnect(server: McpServer) {
  busy.value = server.name
  try {
    await disconnectMcpServer(props.projectId, server.name)
    await load()
  } catch (e) {
    fail(e, t('work.mcp.disconnectFailed'))
  } finally {
    busy.value = ''
  }
}

async function save(name: string) {
  const value = values[name]?.trim()
  if (!value) return
  busy.value = name
  try {
    await setMcpSecret(props.projectId, name, value)
    values[name] = ''
    await load()
  } catch (e) {
    fail(e, t('work.mcp.saveFailed'))
  } finally {
    busy.value = ''
  }
}

async function clear(name: string) {
  busy.value = name
  try {
    await clearMcpSecret(props.projectId, name)
    await load()
  } catch (e) {
    fail(e, t('work.mcp.saveFailed'))
  } finally {
    busy.value = ''
  }
}

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

// 从授权服务器回来：把结果说一次，再从地址栏拿掉，刷新不会再说一遍。
function callbackParams(raw: unknown): Record<string, unknown> {
  try {
    const parsed: unknown = typeof raw === 'string' ? JSON.parse(raw) : null
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : {}
  } catch {
    return {}
  }
}

function failureText(name: string, failure: unknown, params: unknown): string {
  // 不认识的 code（或者没有 key 的那种拒绝，服务端发 failed）只说连接失败，不把 code 念出来。
  const reason = renderNoticeMessage({ key: String(failure), params: callbackParams(params) }, '')
  return reason ? t('work.mcp.connectFailedWith', { name, reason }) : t('work.mcp.connectFailedNamed', { name })
}

function takeCallbackResult() {
  const route = nav?.route
  if (!route) return
  const { mcp, mcp_result: result, mcp_error: failure, mcp_error_params: failureParams, ...rest } = route.query
  if (!result && !failure) return
  const name = String(mcp ?? '')
  if (failure) toast.error(failureText(name, failure, failureParams))
  else toast.success(t('work.mcp.connectedNotice', { name }))
  // 换掉地址、不在身后留一条一样的：`hash` 原样带着，别把锚点也一起抹掉。
  void nav?.navigate({ query: rest, hash: route.hash }, { replace: true })
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
        <BaseEmptyState v-else-if="!list.servers.length" size="inline" :title="t('work.mcp.empty')" />
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
