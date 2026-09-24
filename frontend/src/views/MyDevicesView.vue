<script setup lang="ts">
// 「我的设备 / Agent」(P3 Phase B item 4): the machines the signed-in human enrolled
// via the device flow. List them with liveness, rename/unbind, and open the 现场 of any
// agent (screen) currently running on them — a read-only real terminal in the browser.
import type { DeviceScreen, MyDevice, MyTeam } from '../cx_types'

import { computed, onMounted, ref, watch } from 'vue'

import { listMyDevices, listMyTeams, renameMyDevice, unbindMyDevice } from '../api'
import DeviceLiveViewer from '../components/DeviceLiveViewer.vue'
import {
  connectThisComputer,
  desktopBridge,
  downloadsForThisComputer,
  isThisComputer,
  setAutoConnect,
  thisComputer,
} from '../lib/desktop'

import accountService from '@/services/account'

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

// This page is the 认证 (enrollment) layer: enroll / rename / forget machines, and
// see at a glance which teams each machine serves. 归属 (加机器/移出) lives on each
// team's 「算力」 page — the chips here are read-only links into those pages.
const myTeams = ref<MyTeam[]>([])
function teamName(id: number): string {
  return myTeams.value.find((t) => t.id === id)?.name ?? `团队 #${id}`
}
// The team page lives at its handle; a team you are not in has no page for you.
function teamRoute(id: number) {
  const handle = myTeams.value.find((t) => t.id === id)?.handle
  return handle ? { name: 'TeamsDetailCompute', params: { handle } } : undefined
}

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
const installCommand = computed(() => `curl -fsSL ${window.location.origin}/connector/install.sh | sh`)

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

// Inside the desktop app (desktop/) this computer connects on its own at sign-in
// (lib/desktop.ts); the button here is for connecting it again by hand. Either
// way the progress is the shared `thisComputer` state.
const desktop = desktopBridge()
const downloads = downloadsForThisComputer()

async function connectThisMachine() {
  const userId = accountService.user?.id
  if (userId !== undefined) setAutoConnect(userId, true)
  await connectThisComputer()
}

// However the connection started, once it ends the list is reloaded until the
// computer shows up online — the service dials in a moment after it starts.
watch(
  () => thisComputer.connecting,
  async (connecting) => {
    if (connecting || thisComputer.error) return
    addDeviceOpen.value = false
    for (let i = 0; i < 10; i++) {
      await load()
      if (devices.value.some((d) => d.online)) break
      await new Promise((r) => setTimeout(r, 1500))
    }
  }
)

async function load() {
  // Client-side gate: the device UI is only meaningful for a signed-in human. When
  // signed out we show the gate banner instead of firing an inevitably-401 request.
  if (!isLoggedIn.value) return
  loading.value = true
  error.value = null
  try {
    devices.value = (await listMyDevices()).devices
    // Team names for the read-only chips — best-effort, never blocks the list.
    myTeams.value = await listMyTeams().catch(() => [])
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

// Unbind confirmation runs through an in-app dialog (not the browser's native
// confirm(), which shows an ugly "localhost:5200 says…" chrome and can't be styled).
const unbindTarget = ref<MyDevice | null>(null)
const unbinding = ref(false)

function askUnbind(d: MyDevice) {
  unbindTarget.value = d
}

async function confirmUnbind() {
  const d = unbindTarget.value
  if (!d) return
  unbinding.value = true
  try {
    await unbindMyDevice(d.device_id)
    const userId = accountService.user?.id
    if (desktop && userId !== undefined && (await isThisComputer(d.device_id))) setAutoConnect(userId, false)
    devices.value = devices.value.filter((x) => x.device_id !== d.device_id)
    unbindTarget.value = null
  } catch (e) {
    error.value = e instanceof Error ? e.message : '解绑失败'
  } finally {
    unbinding.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="devices-page fill-height overflow-y-auto">
    <v-container class="py-6" style="max-width: 900px">
      <div class="mb-6 d-flex align-center">
        <div>
          <div class="t-eyebrow mb-1">设备</div>
          <h1 class="t-page-title">我的设备</h1>
        </div>
        <v-spacer />
        <v-btn variant="text" icon="mdi-refresh" class="mr-1" :loading="loading" @click="load" />
        <v-btn v-if="isLoggedIn" color="primary" variant="flat" prepend-icon="mdi-plus" @click="addDeviceOpen = true">
          添加设备
        </v-btn>
      </div>

      <v-alert v-if="!isLoggedIn" type="info" density="comfortable" class="mb-4"> 登录后即可管理已接入的设备 </v-alert>

      <template v-else>
        <v-alert v-if="error" type="error" density="comfortable" class="mb-4" closable @click:close="error = null">
          {{ error }}
        </v-alert>

        <!-- Big spinner only on the FIRST load (list still empty). A refresh with data
           already on screen keeps the list mounted — the refresh button spins instead
           — so re-fetching never tears the list down and flashes. -->
        <div v-if="loading && devices.length === 0" class="d-flex justify-center py-10">
          <v-progress-circular indeterminate color="primary" />
        </div>

        <div v-else-if="devices.length === 0 && desktop" class="empty-state text-center py-10">
          <v-icon size="34" class="mb-3 c-muted">mdi-laptop</v-icon>
          <div class="t-body c-muted mb-1">暂无已连接的设备</div>
          <div class="t-caption c-muted mb-5">把这台电脑接入后，智能体就能在这里干活</div>
          <v-btn color="primary" variant="flat" :loading="thisComputer.connecting" @click="connectThisMachine"
            >接入这台电脑</v-btn
          >
          <div v-if="thisComputer.connecting" class="t-caption c-muted mt-3">{{ thisComputer.step }}</div>
          <div v-if="thisComputer.error" class="t-caption c-danger mt-3">{{ thisComputer.error }}</div>
        </div>

        <div v-else-if="devices.length === 0" class="empty-state text-center py-10">
          <v-icon size="34" class="mb-3 c-muted">mdi-laptop</v-icon>
          <div class="t-body c-muted mb-1">暂无已连接的设备</div>
          <div class="t-caption c-muted mb-5">
            Mac 和 Windows 电脑可以在「添加设备」里下载桌面端一键接入；其他机器运行下面这条命令，按提示批准
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

          <v-btn color="primary" variant="flat" prepend-icon="mdi-plus" @click="addDeviceOpen = true"> 添加设备 </v-btn>
        </div>

        <v-card v-for="d in devices" :key="d.device_id" class="mb-3 pa-4" variant="outlined">
          <div class="d-flex align-center">
            <v-icon :color="d.online ? 'success' : 'grey'" size="12" class="mr-2"> mdi-circle </v-icon>

            <template v-if="renaming === d.device_id">
              <v-text-field
                v-model="draftName"
                autocomplete="off"
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
              <v-btn variant="text" size="x-small" icon="mdi-pencil" class="ml-1" @click="startRename(d)" />
            </template>

            <v-spacer />
            <span class="t-caption mr-3" :class="d.online ? 'text-success font-weight-medium' : 'c-muted'">
              {{ d.online ? '在线' : '离线' }}
            </span>
            <v-btn variant="text" size="small" color="error" @click="askUnbind(d)"> 解绑 </v-btn>
          </div>

          <!-- A device is pure compute (算力节点), not an agent. Which agents run on it
             are the 现场 chips below — each screen carries its own agent identity. -->
          <div class="t-caption c-muted mt-1">
            设备 ID · <span style="font-family: monospace">{{ d.device_id }}</span>
          </div>

          <!-- Read-only 归属 overview: which teams this machine serves (personal team
             included). Registering/removing happens on each team's 「算力」 page —
             each chip links straight there. -->
          <div class="mt-3">
            <div class="t-caption c-muted mb-1">正在为这些团队提供算力</div>
            <div class="d-flex flex-wrap align-center ga-2">
              <v-chip
                v-for="tid in d.team_ids"
                :key="tid"
                size="small"
                variant="tonal"
                color="primary"
                :to="teamRoute(tid)"
              >
                <v-icon start size="14">mdi-account-group</v-icon>
                {{ teamName(tid) }}
              </v-chip>
              <span v-if="!d.team_ids.length" class="t-caption c-muted">
                暂无团队在用，可在团队页面的「算力」里添加
              </span>
            </div>
          </div>

          <div v-if="d.screens.length" class="mt-3">
            <div class="t-caption c-muted mb-1">运行中的现场</div>
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
        <!-- In the desktop app this computer connects in place; in a browser a Mac or
           Windows computer gets the app, and any other machine (a server, Linux)
           keeps the terminal route. -->
        <template v-if="desktop">
          <div class="t-title mt-3 mb-1">这台电脑</div>
          <div class="t-caption c-muted mb-3">自动安装连接程序并完成批准，不用打开终端</div>
          <v-btn color="primary" variant="flat" :loading="thisComputer.connecting" @click="connectThisMachine"
            >接入这台电脑</v-btn
          >
          <div v-if="thisComputer.connecting" class="t-caption c-muted mt-2">{{ thisComputer.step }}</div>
          <div v-if="thisComputer.error" class="t-caption c-danger mt-2">{{ thisComputer.error }}</div>
        </template>
        <template v-else>
          <div class="t-title mt-3 mb-1">Mac 或 Windows 电脑</div>
          <div class="t-caption c-muted mb-3">下载桌面端，登录后点「接入这台电脑」，不用打开终端</div>
          <div class="d-flex flex-wrap ga-2">
            <v-btn
              v-for="(d, i) in downloads"
              :key="d.href"
              :color="i === 0 ? 'primary' : undefined"
              :variant="i === 0 ? 'flat' : 'outlined'"
              prepend-icon="mdi-download"
              :href="d.href"
            >
              {{ d.label }}
            </v-btn>
          </div>
          <div class="t-caption c-muted mt-2">
            第一次打开若被系统拦下：Mac 到「系统设置 → 隐私与安全性」点「仍要打开」，Windows 点「更多信息 → 仍要运行」
          </div>
        </template>

        <div class="t-title mt-6 mb-1">{{ desktop ? '其他机器' : '服务器或 Linux' }}</div>
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
            <span>在你想接入的机器上运行这条命令</span>
          </li>
          <li>
            <span class="steps__n">2</span>
            <span>按提示批准接入（<code>cheesehost auth login</code>）</span>
          </li>
          <li>
            <span class="steps__n">3</span>
            <span>设备出现在这个页面，可看现场、重命名或解绑</span>
          </li>
        </ol>

        <div class="d-flex justify-end mt-4">
          <v-btn variant="flat" color="primary" @click="addDeviceOpen = false">确定</v-btn>
        </div>
      </v-card>
    </v-dialog>

    <v-dialog :model-value="liveScreen !== null" max-width="900" @update:model-value="liveScreen = null">
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

    <!-- Unbind confirmation — an in-app dialog, not the browser's native confirm(). -->
    <v-dialog :model-value="unbindTarget !== null" max-width="440" @update:model-value="unbindTarget = null">
      <v-card v-if="unbindTarget" class="pa-5">
        <div class="d-flex align-center mb-3">
          <v-icon color="error" class="mr-2">mdi-link-variant-off</v-icon>
          <span class="t-title">解绑设备</span>
        </div>
        <div class="t-body mb-1">
          确定解绑设备「<strong>{{ unbindTarget.name }}</strong
          >」吗？
        </div>
        <div class="t-caption c-muted mb-5">它的登录令牌将立即失效，该机器需重新接入才能再次连接</div>
        <div class="d-flex justify-end">
          <v-btn variant="text" class="mr-2" @click="unbindTarget = null">取消</v-btn>
          <v-btn color="error" variant="flat" :loading="unbinding" @click="confirmUnbind"> 解绑 </v-btn>
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
  border: 1px solid var(--accent-wash);
  background: var(--accent-wash);
  border-radius: var(--radius-md);
}
.install-cmd__code {
  flex: 1 1 auto;
  min-width: 0;
  overflow-x: auto;
  white-space: nowrap;
  font-family: 'SF Mono', ui-monospace, Menlo, Consolas, monospace;
  font-size: 13px;
  color: var(--accent-ink);
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
  color: var(--ink);
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
  /* on-primary —— 琥珀填充上的字全站只有这一种做法。浅色下 Vuetify 对 #F57F17 推出来的就是 #fff，所以这里仍然是
     白字，2.65:1，低于 AA 的 4.5:1。这是已知豁免，不是漏掉的 bug：2026-08-16 项目负责
     人拍板「保持白字」—— 改成深墨确实能到 5.84:1，但序号上的字会从白变深、观感肉眼可
     见地变，而品牌琥珀 #F57F17 本身已锁定不动；同一次拍板里，琥珀选中指示条的 2.49:1
     也按同样理由接受了。深色侧不受影响：on-primary 对 #FFA733 推成 #000，10.8:1，过 AA。
     所以：别把它「修好」成钉死的深墨 —— 那是被推翻过的方案。 */
  color: rgb(var(--v-theme-on-primary));
  background: var(--accent);
}
.steps code {
  font-family: 'SF Mono', ui-monospace, Menlo, Consolas, monospace;
  font-size: 12.5px;
  background: var(--accent-wash);
  color: var(--accent-ink);
  padding: 1px 5px;
  border-radius: var(--radius-sm);
}
</style>
