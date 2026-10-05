<script setup lang="ts">
// 「我的设备 / Agent」(P3 Phase B item 4): the machines the signed-in human enrolled
// via the device flow. 取数（设备清单、团队名）、改名/解绑的请求、这台电脑的接入、
// 命令面板、复制反馈都在这一半；画面在 MyDevicesViewView.vue，只收 props 只发事件。
import type { MyDevice, MyTeam } from '../cx_types'

import { computed, onMounted, ref, watch } from 'vue'

import { listMyDevices, listMyTeams, renameMyDevice, screenWsUrl, unbindMyDevice } from '../api'
import {
  connectThisComputer,
  desktopBridge,
  downloadsForThisComputer,
  isThisComputer,
  setAutoConnect,
  thisComputer,
} from '../lib/desktop'

import MyDevicesViewView from './MyDevicesViewView.vue'

import { useCommands } from '@/commands'
import { copyText } from '@/commands/copy'
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
// team's 「工作电脑」 page — the chips there are read-only links into those pages.
const myTeams = ref<MyTeam[]>([])

// Inline rename state, keyed by device_id.
const renaming = ref<string | null>(null)
const draftName = ref('')

const addDeviceOpen = ref(false)

// 复制成的说法交给共享的复制助手（一条 toast），按钮不再自己换成「已复制」——
// 全站复制只有这一种反馈（docs/design-system.md §3.11）。
async function copyInstall(command: string) {
  await copyText(command, t('account.devices.copied'))
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
  <MyDevicesViewView
    :is-logged-in="isLoggedIn"
    :devices="devices"
    :my-teams="myTeams"
    :loading="loading"
    :error="error"
    :desktop="desktop"
    :downloads="downloads"
    :connecting="thisComputer.connecting"
    :connect-step="thisComputer.step"
    :connect-error="thisComputer.error"
    :renaming="renaming"
    :draft-name="draftName"
    :add-open="addDeviceOpen"
    :unbind-open="unbindOpen"
    :unbind-title="unbindTitle"
    :unbinding="unbinding"
    :make-url="screenWsUrl"
    @refresh="load"
    @dismiss-error="error = null"
    @update:draft-name="draftName = $event"
    @update:add-open="addDeviceOpen = $event"
    @update:unbind-open="unbindOpen = $event"
    @connect-this="connectThisMachine"
    @copy="copyInstall"
    @start-rename="startRename"
    @save-rename="saveRename"
    @ask-unbind="askUnbind"
    @confirm-unbind="confirmUnbind"
  />
</template>
