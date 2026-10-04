<script setup lang="ts">
// 「我的设备 / Agent」(P3 Phase B item 4): the machines the signed-in human enrolled
// via the device flow. List them with liveness, rename/unbind, and open the 现场 of any
// agent (screen) currently running on them — a read-only real terminal in the browser.
import type { MenuAction } from '../components/common/menuAction'
import type { DeviceScreen, MyDevice, MyTeam } from '../cx_types'

import { computed, onMounted, ref, watch } from 'vue'
import { useDisplay } from 'vuetify'

import { listMyDevices, listMyTeams, renameMyDevice, unbindMyDevice } from '../api'
import AdaptiveDialog from '../components/common/AdaptiveDialog.vue'
import AdaptiveMenu from '../components/common/AdaptiveMenu.vue'
import DeviceLiveViewer from '../components/DeviceLiveViewer.vue'
import {
  connectThisComputer,
  desktopBridge,
  downloadsForThisComputer,
  isThisComputer,
  setAutoConnect,
  thisComputer,
} from '../lib/desktop'

import { useCommands } from '@/commands'
import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import ConfirmDialog from '@/components/base/ConfirmDialog.vue'
import { t } from '@/i18n'
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
// team's 「工作电脑」 page — the chips here are read-only links into those pages.
const myTeams = ref<MyTeam[]>([])
function teamName(id: number): string {
  return myTeams.value.find((t) => t.id === id)?.name ?? t('account.devices.teamFallback', { id })
}
// 自己名下（只有自己的那个团队）以自己的昵称出现，图标也是一个人而不是一群人。
const isOwn = (id: number) => myTeams.value.some((team) => team.id === id && team.personal)
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
// One per shell: install.sh for Mac and Linux, install.ps1 for Windows.
const installCommands = computed(() => [
  { os: t('account.devices.os.unix'), command: `curl -fsSL ${window.location.origin}/connector/install.sh | sh` },
  { os: t('account.devices.os.windows'), command: `irm ${window.location.origin}/connector/install.ps1 | iex` },
])
const copied = ref<string | null>(null)

async function copyInstall(command: string) {
  try {
    await navigator.clipboard.writeText(command)
    copied.value = command
    setTimeout(() => (copied.value = null), 1600)
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
    const msg = e instanceof Error ? e.message : t('account.devices.loadFailed')
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
    error.value = e instanceof Error ? e.message : t('account.devices.renameFailed')
  } finally {
    renaming.value = null
  }
}

// Unbind confirmation runs through an in-app dialog (not the browser's native
// confirm(), which shows an ugly "localhost:5200 says…" chrome and can't be styled).
const unbindTarget = ref<MyDevice | null>(null)
const unbinding = ref(false)

// The confirm box is open exactly while a device is picked to unlink; closing it
// (cancel) clears the target. Title only resolves while a device is picked.
const unbindOpen = computed({
  get: () => unbindTarget.value !== null,
  set: (open) => {
    if (!open) unbindTarget.value = null
  },
})
const unbindTitle = computed(() =>
  unbindTarget.value ? t('account.devices.unbindTitle', { name: unbindTarget.value.name }) : ''
)

// 手机上一台设备的操作（改名、解绑）收进行尾的 ⋯（底部面板）：名字旁那颗小铅笔和
// 行尾的「解绑」都比手指小，挨着「在线」两个字也容易按错。
const { mdAndUp } = useDisplay()
function deviceActions(d: MyDevice): MenuAction[] {
  return [
    { key: 'rename', label: t('account.devices.rename'), icon: 'mdi-pencil-outline', onSelect: () => startRename(d) },
    {
      key: 'unbind',
      label: t('account.devices.unbind'),
      icon: 'mdi-link-variant-off',
      danger: true,
      onSelect: () => askUnbind(d),
    },
  ]
}

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
    error.value = e instanceof Error ? e.message : t('account.devices.unbindFailed')
  } finally {
    unbinding.value = false
  }
}

onMounted(load)

// 「添加设备」也能从命令面板去；页面上那颗按钮在标题旁边。
useCommands(() =>
  isLoggedIn.value
    ? [
        {
          id: 'devices.add',
          title: t('account.devices.add'),
          icon: 'mdi-plus',
          run: () => (addDeviceOpen.value = true),
        },
      ]
    : []
)
</script>

<template>
  <div class="settings-page">
    <header class="devices__head">
      <div>
        <h1 class="t-page-title">{{ t('account.settings.devices') }}</h1>
        <p class="settings-page__lede">{{ t('account.devices.lede') }}</p>
      </div>
      <div v-if="isLoggedIn" class="devices__actions">
        <BaseButton
          icon="mdi-refresh"
          size="sm"
          :loading="loading"
          :aria-label="t('account.devices.refresh')"
          :title="t('account.devices.refresh')"
          @click="load"
        />
        <BaseButton kind="primary" prepend-icon="mdi-plus" @click="addDeviceOpen = true">
          {{ t('account.devices.add') }}
        </BaseButton>
      </div>
    </header>

    <BaseEmptyState v-if="!isLoggedIn" size="inline" class="settings-empty" :title="t('account.devices.signInFirst')" />

    <template v-else>
      <v-alert v-if="error" type="error" density="comfortable" closable @click:close="error = null">
        {{ error }}
      </v-alert>

      <section class="settings-card">
        <div class="settings-card__title">{{ t('account.devices.listTitle') }}</div>

        <!-- 第一次加载（列表还是空的）才转圈；已经有设备时刷新不拆列表，转的是标题旁那颗
             刷新按钮，所以再取一次不会闪。 -->
        <div v-if="loading && devices.length === 0" class="d-flex justify-center py-6">
          <v-progress-circular indeterminate size="24" />
        </div>

        <BaseEmptyState
          v-else-if="devices.length === 0"
          size="inline"
          class="settings-empty devices__empty"
          :title="t('account.devices.empty')"
        >
          <!-- 在桌面 app 里，最直接的是把这台电脑接进来。 -->
          <template v-if="desktop">
            <BaseButton kind="secondary" :loading="thisComputer.connecting" @click="connectThisMachine">
              {{ t('account.devices.connectThis') }}
            </BaseButton>
            <span v-if="thisComputer.connecting">{{ thisComputer.step }}</span>
            <span v-if="thisComputer.error" class="c-danger">{{ thisComputer.error }}</span>
          </template>
        </BaseEmptyState>

        <div v-for="d in devices" :key="d.device_id" class="device">
          <div class="device__line">
            <span class="device__dot" :class="{ 'device__dot--on': d.online }" aria-hidden="true" />
            <v-text-field
              v-if="renaming === d.device_id"
              v-model="draftName"
              autocomplete="off"
              density="compact"
              variant="outlined"
              hide-details
              autofocus
              class="device__rename"
              @keyup.enter="saveRename(d)"
              @blur="saveRename(d)"
            />
            <template v-else>
              <span class="device__name">{{ d.name }}</span>
              <BaseButton
                v-if="mdAndUp"
                size="sm"
                icon="mdi-pencil-outline"
                :aria-label="t('account.devices.rename')"
                :title="t('account.devices.rename')"
                @click="startRename(d)"
              />
            </template>
            <span class="device__state" :class="{ 'device__state--on': d.online }">
              {{ d.online ? t('account.devices.online') : t('account.devices.offline') }}
            </span>
            <BaseButton v-if="mdAndUp" kind="ghost" size="sm" @click="askUnbind(d)">
              {{ t('account.devices.unbind') }}
            </BaseButton>
            <AdaptiveMenu v-else :actions="deviceActions(d)" :title="d.name">
              <template #activator="{ props: menuProps }">
                <BaseButton
                  v-bind="menuProps"
                  icon="mdi-dots-horizontal"
                  size="sm"
                  class="tap-target"
                  :aria-label="t('account.devices.more')"
                />
              </template>
            </AdaptiveMenu>
          </div>

          <!-- 一台设备只是一台机器，不是队友：在它上面跑的是哪些队友，看下面的「现场」。 -->
          <div class="device__meta">
            {{ t('account.devices.deviceId') }} · <code>{{ d.device_id }}</code>
          </div>

          <!-- 只读的归属：这台机器在给哪些团队、以及自己名下的项目用。加机器、移出在各自的
               「工作电脑」页里做，每一枚都直接链过去。 -->
          <div class="device__block">
            <div class="device__label">{{ t('account.devices.teams') }}</div>
            <div class="device__chips">
              <v-chip v-for="tid in d.team_ids" :key="tid" size="small" variant="outlined" :to="teamRoute(tid)">
                <v-icon start size="14">{{ isOwn(tid) ? 'mdi-account-outline' : 'mdi-account-group' }}</v-icon>
                {{ teamName(tid) }}
              </v-chip>
              <span v-if="!d.team_ids.length" class="device__meta">{{ t('account.devices.noTeams') }}</span>
            </div>
          </div>

          <div v-if="d.screens.length" class="device__block">
            <div class="device__label">{{ t('account.devices.screens') }}</div>
            <div class="device__chips">
              <v-chip v-for="s in d.screens" :key="s.sid" variant="outlined" size="small" @click="liveScreen = s">
                <v-icon start size="14">mdi-monitor-eye</v-icon>
                {{ t('account.devices.watch', { handle: s.agent_handle }) }}
              </v-chip>
            </div>
          </div>
        </div>
      </section>

      <section class="settings-card">
        <div class="settings-card__title">{{ t('account.devices.commandsTitle') }}</div>
        <div class="settings-card__desc">{{ t('account.devices.commandsDesc') }}</div>
        <div v-for="c in installCommands" :key="c.command" class="srow">
          <span class="srow__k">{{ c.os }}</span>
          <div class="install-cmd">
            <code class="install-cmd__code">{{ c.command }}</code>
            <BaseButton
              kind="ghost"
              size="sm"
              :prepend-icon="copied === c.command ? 'mdi-check' : 'mdi-content-copy'"
              @click="copyInstall(c.command)"
            >
              {{ copied === c.command ? t('account.devices.copied') : t('account.devices.copy') }}
            </BaseButton>
          </div>
        </div>
      </section>
    </template>

    <!-- Add a device: how this computer or another machine connects. -->
    <AdaptiveDialog
      v-model="addDeviceOpen"
      :title="t('account.devices.add')"
      :primary-label="t('account.devices.done')"
      @primary="addDeviceOpen = false"
    >
      <!-- In the desktop app this computer connects in place; in a browser, Mac and Windows
           install the desktop app and every other machine (servers, Linux) runs the command. -->
      <template v-if="desktop">
        <div class="t-title mt-3 mb-1">{{ t('account.devices.thisComputer') }}</div>
        <div class="t-caption c-muted mb-3">{{ t('account.devices.thisComputerHint') }}</div>
        <BaseButton kind="primary" :loading="thisComputer.connecting" @click="connectThisMachine">
          {{ t('account.devices.connectThis') }}
        </BaseButton>
        <div v-if="thisComputer.connecting" class="t-caption c-muted mt-2">{{ thisComputer.step }}</div>
        <div v-if="thisComputer.error" class="t-caption c-danger mt-2">{{ thisComputer.error }}</div>
      </template>
      <template v-else>
        <div class="t-title mt-3 mb-1">{{ t('account.devices.desktopTitle') }}</div>
        <div class="t-caption c-muted mb-3">{{ t('account.devices.desktopHint') }}</div>
        <div class="d-flex flex-wrap ga-2">
          <BaseButton
            v-for="(d, i) in downloads"
            :key="d.href"
            :kind="i === 0 ? 'primary' : 'secondary'"
            prepend-icon="mdi-download"
            :href="d.href"
          >
            {{ t(d.labelKey) }}
          </BaseButton>
        </div>
        <div class="t-caption c-muted mt-2">{{ t('account.devices.gatekeeper') }}</div>
      </template>

      <div class="t-title mt-6 mb-1">
        {{ desktop ? t('account.devices.otherMachines') : t('account.devices.serverTitle') }}
      </div>
      <div v-for="c in installCommands" :key="c.command" class="mb-3">
        <div class="t-caption c-muted mb-1">{{ c.os }}</div>
        <div class="install-cmd">
          <code class="install-cmd__code">{{ c.command }}</code>
          <BaseButton
            kind="ghost"
            size="sm"
            :prepend-icon="copied === c.command ? 'mdi-check' : 'mdi-content-copy'"
            @click="copyInstall(c.command)"
          >
            {{ copied === c.command ? t('account.devices.copied') : t('account.devices.copy') }}
          </BaseButton>
        </div>
      </div>

      <ol class="steps">
        <li>{{ t('account.devices.step1') }}</li>
        <li>{{ t('account.devices.step2') }}</li>
        <li>{{ t('account.devices.step3') }}</li>
      </ol>
    </AdaptiveDialog>

    <v-dialog :model-value="liveScreen !== null" max-width="900" @update:model-value="liveScreen = null">
      <v-card v-if="liveScreen" class="pa-3">
        <div class="d-flex align-center mb-2">
          <span class="t-title">{{ t('account.devices.liveTitle', { handle: liveScreen.agent_handle }) }}</span>
          <v-spacer />
          <BaseButton icon="mdi-close" :aria-label="t('account.devices.close')" @click="liveScreen = null" />
        </div>
        <div style="height: 60vh">
          <DeviceLiveViewer :sid="liveScreen.sid" />
        </div>
      </v-card>
    </v-dialog>

    <!-- Ask before unlinking: an in-app dialog, not the browser's native confirm(). -->
    <ConfirmDialog
      v-model="unbindOpen"
      :title="unbindTitle"
      :confirm-label="t('account.devices.unbind')"
      danger
      :loading="unbinding"
      @confirm="confirmUnbind"
    >
      {{ t('account.devices.unbindHint') }}
    </ConfirmDialog>
  </div>
</template>

<style scoped src="@/styles/settings-card.css"></style>
<style scoped>
.devices__head {
  display: flex;
  gap: 16px;
  align-items: flex-end;
  justify-content: space-between;
}

.devices__actions {
  display: flex;
  flex-shrink: 0;
  gap: 8px;
  align-items: center;
}

.devices__empty {
  display: flex;
  flex-direction: column;
  gap: 12px;
  align-items: flex-start;
}

.device {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 14px 24px 16px;
  border-top: 1px solid var(--line);
}

.device__line {
  display: flex;
  gap: 8px;
  align-items: center;
}

.device__dot {
  width: 8px;
  height: 8px;
  border-radius: var(--radius-pill);
  background: var(--faint);
}

.device__dot--on {
  background: var(--ok);
}

.device__name {
  color: var(--ink);
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
}

.device__rename {
  max-width: 260px;
}

.device__state {
  margin-left: auto;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}

.device__state--on {
  color: var(--ok-ink);
}

.device__meta {
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}

.device__meta code {
  font-family: var(--font-mono);
}

.device__block {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.device__label {
  color: var(--faint);
  font-size: 12px;
  line-height: var(--lh-12);
}

.device__chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

/* 一行可以复制的命令：等宽字放在浅灰底里，复制按钮贴在后面。手机宽度下命令折行、
   复制按钮另起一行，见下面 599.98px 的媒体查询。 */
.install-cmd {
  display: flex;
  gap: 8px;
  align-items: center;
  min-width: 0;
  padding: 4px 4px 4px 12px;
  border-radius: var(--radius-md);
  background: var(--fill);
}

.install-cmd__code {
  flex: 1 1 auto;
  min-width: 0;
  overflow-x: auto;
  color: var(--ink);
  font-family: var(--font-mono);
  font-size: 13px;
  line-height: var(--lh-13);
  white-space: nowrap;
}

.srow:has(.install-cmd) {
  grid-template-columns: 180px minmax(0, 1fr);
}

.steps {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 0;
  padding-left: 20px;
  color: var(--text);
  font-size: 14px;
  line-height: var(--lh-14);
}

/* 断点对齐共享 token（`styles/breakpoints.scss`）：599.98 → 767.98，和这一页
   一起加载的 `settings-card.css` 同一条线。 */
@media (max-width: 767.98px) {
  .device {
    padding: 12px 16px 14px;
  }

  .srow:has(.install-cmd) {
    grid-template-columns: minmax(0, 1fr);
  }

  /* 命令整条占一行、断行折开，复制按钮落到下一行并靠右。
     390px 上原来是一行两件：命令被按钮挤掉一半，剩下那半截横着滚——滚动条
     看不见、也没有任何提示，读到的是一个从中间断掉的 URL。 */
  .install-cmd {
    flex-wrap: wrap;
    row-gap: 2px;
    justify-content: flex-end;
  }

  .install-cmd__code {
    flex: 1 1 100%;
    overflow-x: visible;
    white-space: normal;
    word-break: break-all;
  }
}
</style>
