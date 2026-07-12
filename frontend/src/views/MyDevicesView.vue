<script setup lang="ts">
// 「我的设备 / Agent」(P3 Phase B item 4): the machines the signed-in human enrolled
// via the device flow. List them with liveness, rename/unbind, and open the 现场 of any
// agent (screen) currently running on them — a read-only real terminal in the browser.
import { computed, onMounted, ref } from 'vue'
import {
  listMyDevices,
  renameMyDevice,
  unbindMyDevice,
} from '../api'
import accountService from '@/services/account'
import type { DeviceScreen, MyDevice } from '../cx_types'
import DeviceLiveViewer from '../components/DeviceLiveViewer.vue'

// The real logged-in session, resolved the same way the rest of the app resolves
// it: AccountService.loggedIn (set from localStorage `accessToken` + `user` at
// boot). We also accept the raw localStorage credential as a fallback so the gate
// is correct even before AccountService.init() has finished its async warm-up.
const isLoggedIn = computed(() => {
  if (accountService.loggedIn) return true
  try {
    return !!localStorage.getItem('accessToken')
  } catch {
    return false
  }
})

const devices = ref<MyDevice[]>([])
const loading = ref(false)
const error = ref<string | null>(null)

// The screen whose 现场 is open in the viewer dialog.
const liveScreen = ref<DeviceScreen | null>(null)

// Inline rename state, keyed by device_id.
const renaming = ref<string | null>(null)
const draftName = ref('')

// 「添加设备」flow. The install one-liner the user runs on the *target* machine.
// The origin is derived from where the app is actually served (window.location):
// dev proxies /connector → :8799, prod serves it same-origin, so this is always
// the reachable backend from the user's browser — never hardcoded to a dead port.
const addDeviceOpen = ref(false)
const copied = ref(false)
const installCommand = computed(
  () => `curl -fsSL ${window.location.origin}/connector/install.sh | sh`,
)

async function copyInstall() {
  try {
    await navigator.clipboard.writeText(installCommand.value)
    copied.value = true
    setTimeout(() => (copied.value = false), 1600)
  } catch {
    // Clipboard blocked (insecure context / permissions) — leave the command
    // visible so the user can still select and copy it by hand.
  }
}

async function load() {
  // Client-side gate: the device UI is only meaningful for a signed-in human. When
  // signed out we show the gate banner instead of firing an inevitably-401 request.
  if (!isLoggedIn.value) return
  loading.value = true
  error.value = null
  try {
    devices.value = (await listMyDevices()).devices
  } catch (e) {
    const msg = e instanceof Error ? e.message : '加载设备失败'
    // We are (client-side) authoritatively signed in, so the connector's
    // "requires a logged-in user" gate is not the truth about the session — it
    // means the connector has no owner record for us yet (no device enrolled).
    // Never surface that as the "please log in" banner; fall through to the
    // empty-state, which correctly invites the user to run `cheese link`.
    if (msg.includes('requires a logged-in user')) {
      devices.value = []
    } else {
      error.value = msg
    }
  } finally {
    loading.value = false
  }
}

function startRename(d: MyDevice) {
  renaming.value = d.device_id
  draftName.value = d.name
}

async function saveRename(d: MyDevice) {
  const name = draftName.value.trim()
  if (!name || name === d.name) {
    renaming.value = null
    return
  }
  try {
    const updated = await renameMyDevice(d.device_id, name)
    Object.assign(d, updated)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '重命名失败'
  } finally {
    renaming.value = null
  }
}

async function unbind(d: MyDevice) {
  if (!confirm(`确定解绑设备「${d.name}」吗？它的登录令牌将失效。`)) return
  try {
    await unbindMyDevice(d.device_id)
    devices.value = devices.value.filter((x) => x.device_id !== d.device_id)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '解绑失败'
  }
}

onMounted(load)
</script>

<template>
  <div class="devices-page fill-height overflow-y-auto">
    <v-container class="py-6" style="max-width: 900px">
      <div class="mb-6 d-flex align-center">
        <div>
          <div class="t-eyebrow mb-1">连接器</div>
          <h1 class="t-page-title">我的设备</h1>
        </div>
        <v-spacer />
        <v-btn variant="text" icon="mdi-refresh" class="mr-1" @click="load" />
        <v-btn
          v-if="isLoggedIn"
          color="primary"
          variant="flat"
          prepend-icon="mdi-plus"
          @click="addDeviceOpen = true"
        >
          添加设备
        </v-btn>
      </div>

      <v-alert
        v-if="!isLoggedIn"
        type="info"
        density="comfortable"
        class="mb-4"
      >
        登录后即可管理接入的客户机 / Agent。
      </v-alert>

      <template v-else>
      <v-alert
        v-if="error"
        type="error"
        density="comfortable"
        class="mb-4"
        closable
        @click:close="error = null"
      >
        {{ error }}
      </v-alert>

      <div v-if="loading" class="d-flex justify-center py-10">
        <v-progress-circular indeterminate color="primary" />
      </div>

      <div v-else-if="devices.length === 0" class="empty-state text-center py-10">
        <v-icon size="34" class="mb-3 c-muted">mdi-laptop</v-icon>
        <div class="t-body c-muted mb-1">还没有连接的设备。</div>
        <div class="t-caption c-muted mb-5">
          在你的机器上运行下面这条命令，按提示批准，设备就会出现在这里。
        </div>

        <!-- Copyable install one-liner, right in the empty-state so the user can
             act without hunting for a dialog. -->
        <div class="install-cmd mx-auto mb-4">
          <code class="install-cmd__code">{{ installCommand }}</code>
          <v-btn
            :color="copied ? 'success' : 'primary'"
            variant="text"
            size="small"
            :prepend-icon="copied ? 'mdi-check' : 'mdi-content-copy'"
            @click="copyInstall"
          >
            {{ copied ? '已复制' : '复制' }}
          </v-btn>
        </div>

        <v-btn
          color="primary"
          variant="flat"
          prepend-icon="mdi-plus"
          @click="addDeviceOpen = true"
        >
          添加设备
        </v-btn>
      </div>

      <v-card
        v-for="d in devices"
        :key="d.device_id"
        class="mb-3 pa-4"
        variant="outlined"
      >
        <div class="d-flex align-center">
          <v-icon
            :color="d.online ? 'success' : 'grey'"
            size="12"
            class="mr-2"
          >
            mdi-circle
          </v-icon>

          <template v-if="renaming === d.device_id">
            <v-text-field
              v-model="draftName"
              density="compact"
              variant="outlined"
              hide-details
              autofocus
              style="max-width: 260px"
              @keyup.enter="saveRename(d)"
              @blur="saveRename(d)"
            />
          </template>
          <template v-else>
            <span class="t-title">{{ d.name }}</span>
            <v-btn
              variant="text"
              size="x-small"
              icon="mdi-pencil"
              class="ml-1"
              @click="startRename(d)"
            />
          </template>

          <v-spacer />
          <span class="t-caption c-muted mr-3">
            {{ d.online ? '在线' : '离线' }}
          </span>
          <v-btn
            variant="text"
            size="small"
            color="error"
            @click="unbind(d)"
          >
            解绑
          </v-btn>
        </div>

        <div class="t-caption c-muted mt-1">
          agent 身份:
          <strong v-if="d.agent_handle">@{{ d.agent_handle }}</strong>
          <span v-else>—</span>
        </div>

        <div v-if="d.screens.length" class="mt-3">
          <div class="t-caption c-muted mb-1">运行中的 agent 现场</div>
          <div class="d-flex flex-wrap ga-2">
            <v-chip
              v-for="s in d.screens"
              :key="s.sid"
              color="primary"
              variant="tonal"
              size="small"
              @click="liveScreen = s"
            >
              <v-icon start size="14">mdi-monitor-eye</v-icon>
              看现场 · @{{ s.agent_handle }}
            </v-chip>
          </div>
        </div>
      </v-card>
      </template>
    </v-container>

    <!-- 添加设备: how to enroll this-or-another machine via the device flow. -->
    <v-dialog v-model="addDeviceOpen" max-width="560">
      <v-card class="pa-5">
        <div class="d-flex align-center mb-1">
          <v-icon color="primary" class="mr-2">mdi-laptop-account</v-icon>
          <span class="t-title">添加设备</span>
          <v-spacer />
          <v-btn variant="text" icon="mdi-close" size="small" @click="addDeviceOpen = false" />
        </div>
        <div class="t-caption c-muted mb-4">
          在你想接入的机器上运行下面这条命令，即可把它连接到 CheeseX。
        </div>

        <div class="install-cmd mb-5">
          <code class="install-cmd__code">{{ installCommand }}</code>
          <v-btn
            :color="copied ? 'success' : 'primary'"
            variant="text"
            size="small"
            :prepend-icon="copied ? 'mdi-check' : 'mdi-content-copy'"
            @click="copyInstall"
          >
            {{ copied ? '已复制' : '复制' }}
          </v-btn>
        </div>

        <ol class="steps">
          <li>
            <span class="steps__n">1</span>
            <span>在你的机器上运行这条命令。</span>
          </li>
          <li>
            <span class="steps__n">2</span>
            <span>按提示批准接入（<code>cheesehost auth login</code>）。</span>
          </li>
          <li>
            <span class="steps__n">3</span>
            <span>设备出现在这个页面，可看现场、重命名或解绑。</span>
          </li>
        </ol>

        <div class="d-flex justify-end mt-4">
          <v-btn variant="flat" color="primary" @click="addDeviceOpen = false">知道了</v-btn>
        </div>
      </v-card>
    </v-dialog>

    <v-dialog
      :model-value="liveScreen !== null"
      max-width="900"
      @update:model-value="liveScreen = null"
    >
      <v-card v-if="liveScreen" class="pa-3">
        <div class="d-flex align-center mb-2">
          <span class="t-title">现场 · @{{ liveScreen.agent_handle }}</span>
          <v-spacer />
          <v-btn variant="text" icon="mdi-close" @click="liveScreen = null" />
        </div>
        <div style="height: 60vh">
          <DeviceLiveViewer :sid="liveScreen.sid" />
        </div>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped>
/* Copyable install one-liner: a monospace command in a soft amber-washed pill
   with the copy button riding alongside. Scrolls horizontally on narrow widths
   rather than wrapping the command. */
.install-cmd {
  display: flex;
  align-items: center;
  gap: 8px;
  max-width: 480px;
  padding: 6px 6px 6px 14px;
  border: 1px solid var(--accent-wash, #fdf1e2);
  background: var(--accent-wash, #fdf1e2);
  border-radius: 10px;
}
.install-cmd__code {
  flex: 1 1 auto;
  min-width: 0;
  overflow-x: auto;
  white-space: nowrap;
  font-family: 'SF Mono', ui-monospace, Menlo, Consolas, monospace;
  font-size: 13px;
  color: var(--accent-ink, #9a5413);
  background: transparent;
  text-align: left;
}

/* 1-2-3 steps: numbered amber discs with left-aligned copy. */
.steps {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.steps li {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  color: var(--ink, #191a1c);
  font-size: 14px;
  line-height: 1.5;
}
.steps__n {
  flex: 0 0 auto;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
  color: #fff;
  background: var(--accent, #f57f17);
}
.steps code {
  font-family: 'SF Mono', ui-monospace, Menlo, Consolas, monospace;
  font-size: 12.5px;
  background: var(--accent-wash, #fdf1e2);
  color: var(--accent-ink, #9a5413);
  padding: 1px 5px;
  border-radius: 4px;
}
</style>
