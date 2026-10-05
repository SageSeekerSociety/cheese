<script setup lang="ts">
// 「我的设备 / Agent」的画面：设备清单（在线与否、改名、解绑、团队归属、现场入口）、
// 「添加设备」弹窗和现场终端弹窗。取数、改名/解绑的请求、这台电脑的接入、命令面板
// 和错误 toast 都在容器 MyDevicesView.vue 里 —— 这一半只收 props，只发事件。
//
// 归属 chip 的名字 / 是不是自己 / 链到哪，都在这里由 `myTeams` 算出来（纯函数，不取数、
// 不读地址）；现场 socket 的地址由 `make-url` 回调（容器给的是 api 的 screenWsUrl）
// 在原来调它的时机现取，token 的取值时机和以前一模一样。
import type { MenuAction } from '@/components/common/menuAction'
import type { DeviceScreen, MyDevice, MyTeam } from '@/cx_types'

import { computed, ref } from 'vue'
import { useDisplay } from 'vuetify'

import { useRowMenu } from '@/composables/useRowMenu'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import ConfirmDialog from '@/components/base/ConfirmDialog.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import DeviceLiveViewer from '@/components/DeviceLiveViewer.vue'
import { t } from '@/i18n'

const props = defineProps<{
  isLoggedIn: boolean
  devices: MyDevice[]
  /** 这台机器在给哪些团队用：只读 chip 的名字和去处都从这一份算出来。 */
  myTeams: MyTeam[]
  loading: boolean
  error: string | null
  /** 在桌面 app 里跑的时候，这台电脑可以就地接进来。 */
  desktop: boolean
  /** 桌面 app 的下载项（href 已经指向各平台的安装包）。 */
  downloads: { os: string; labelKey: string; href: string }[]
  /** 这台电脑正在接入时的进度与失败原因（共享状态，容器看着）。 */
  connecting: boolean
  connectStep: string
  connectError: string | null
  /** 正在改名的那台设备，和输入框里草稿的名字。 */
  renaming: string | null
  draftName: string
  addOpen: boolean
  unbindOpen: boolean
  unbindTitle: string
  unbinding: boolean
  /** 现场 socket 的地址怎么拼：容器给的是 api 的 screenWsUrl，这里只负责在对的时机调它。 */
  makeUrl: (sid: string) => string
}>()

const emit = defineEmits<{
  refresh: []
  'dismiss-error': []
  'update:draft-name': [value: string]
  'update:add-open': [value: boolean]
  'update:unbind-open': [value: boolean]
  'connect-this': []
  copy: [command: string]
  'start-rename': [device: MyDevice]
  'save-rename': [device: MyDevice]
  'ask-unbind': [device: MyDevice]
  'confirm-unbind': []
}>()

// 手机上一台设备的操作（改名、解绑）收进行尾的 ⋯（底部面板）：名字旁那颗小铅笔和
// 行尾的「解绑」都比手指小，挨着「在线」两个字也容易按错。
const { mdAndUp } = useDisplay()
// 桌面上改名、解绑摆在行里；右键一台设备弹的是同一份，弹在鼠标那一点上。
const rowMenu = useRowMenu<string>()

// The screen whose 现场 is open in the viewer dialog. 开哪一台是这一半自己的事。
const liveScreen = ref<DeviceScreen | null>(null)

function deviceActions(d: MyDevice): MenuAction[] {
  return [
    {
      key: 'rename',
      label: t('account.devices.rename'),
      icon: 'mdi-pencil-outline',
      onSelect: () => emit('start-rename', d),
    },
    {
      key: 'unbind',
      label: t('account.devices.unbind'),
      icon: 'mdi-link-variant-off',
      danger: true,
      onSelect: () => emit('ask-unbind', d),
    },
  ]
}

// 自己名下（只有自己的那个团队）以自己的昵称出现，图标也是一个人而不是一群人。
function isOwn(id: number) {
  return props.myTeams.some((team) => team.id === id && team.personal)
}
function teamName(id: number): string {
  return props.myTeams.find((team) => team.id === id)?.name ?? t('account.devices.teamFallback', { id })
}
// The team page lives at its handle; a team you are not in has no page for you.
function teamRoute(id: number) {
  const handle = props.myTeams.find((team) => team.id === id)?.handle
  return handle ? { name: 'TeamsDetailCompute', params: { handle } } : undefined
}

// 「添加设备」里那条装到目标机器上的一次性命令。origin 取应用实际被服务的地址
// （window.location）：dev 反代 /connector → :8799，prod 同源，所以永远是浏览器够得着的
// 后端，不会写死一个没人听的端口。One per shell: install.sh for Mac/Linux, install.ps1 for Windows.
const installCommands = computed(() => [
  { os: t('account.devices.os.unix'), command: `curl -fsSL ${window.location.origin}/connector/install.sh | sh` },
  { os: t('account.devices.os.windows'), command: `irm ${window.location.origin}/connector/install.ps1 | iex` },
])
</script>

<template>
  <div class="settings-page">
    <!-- Deliberately not an AppPage: this view is one page of the user settings
         overlay (layouts/user/Settings.vue, meta.settingsOverlay). The overlay
         already owns the content column (components/common/SettingsOverlay,
         `.so__content` — 720 centred, 24 in), so a page frame here would nest a
         second header and a second column; the shared settings-card shell
         (styles/settings-card.css) is this kind of page's frame
         (docs/design-system.md §3.5). -->
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
          @click="$emit('refresh')"
        />
        <BaseButton kind="primary" prepend-icon="mdi-plus" @click="$emit('update:add-open', true)">
          {{ t('account.devices.add') }}
        </BaseButton>
      </div>
    </header>

    <BaseEmptyState v-if="!isLoggedIn" size="inline" class="settings-empty" :title="t('account.devices.signInFirst')" />

    <template v-else>
      <v-alert v-if="error" type="error" density="comfortable" closable @click:close="$emit('dismiss-error')">
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
            <BaseButton kind="secondary" :loading="connecting" @click="$emit('connect-this')">
              {{ t('account.devices.connectThis') }}
            </BaseButton>
            <span v-if="connecting">{{ connectStep }}</span>
            <span v-if="connectError" class="c-danger">{{ connectError }}</span>
          </template>
        </BaseEmptyState>

        <div
          v-for="d in devices"
          :key="d.device_id"
          class="device"
          @contextmenu="mdAndUp && renaming !== d.device_id && rowMenu.open(d.device_id, $event)"
        >
          <AdaptiveMenu v-if="mdAndUp" v-bind="rowMenu.bind(d.device_id)" :actions="deviceActions(d)" :title="d.name">
            <template #activator />
          </AdaptiveMenu>
          <div class="device__line">
            <span class="device__dot" :class="{ 'device__dot--on': d.online }" aria-hidden="true" />
            <v-text-field
              v-if="renaming === d.device_id"
              :model-value="draftName"
              autocomplete="off"
              density="compact"
              variant="outlined"
              hide-details
              autofocus
              class="device__rename"
              @update:model-value="$emit('update:draft-name', String($event))"
              @keyup.enter="$emit('save-rename', d)"
              @blur="$emit('save-rename', d)"
            />
            <template v-else>
              <span class="device__name">{{ d.name }}</span>
              <BaseButton
                v-if="mdAndUp"
                size="sm"
                icon="mdi-pencil-outline"
                :aria-label="t('account.devices.rename')"
                :title="t('account.devices.rename')"
                @click="$emit('start-rename', d)"
              />
            </template>
            <span class="device__state" :class="{ 'device__state--on': d.online }">
              {{ d.online ? t('account.devices.online') : t('account.devices.offline') }}
            </span>
            <BaseButton v-if="mdAndUp" kind="ghost" size="sm" @click="$emit('ask-unbind', d)">
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
            <BaseButton kind="ghost" size="sm" prepend-icon="mdi-content-copy" @click="$emit('copy', c.command)">
              {{ t('account.devices.copy') }}
            </BaseButton>
          </div>
        </div>
      </section>
    </template>

    <!-- Add a device: how this computer or another machine connects. -->
    <AdaptiveDialog
      :model-value="addOpen"
      :title="t('account.devices.add')"
      :primary-label="t('account.devices.done')"
      @update:model-value="$emit('update:add-open', $event)"
      @primary="$emit('update:add-open', false)"
    >
      <!-- In the desktop app this computer connects in place; in a browser, Mac and Windows
           install the desktop app and every other machine (servers, Linux) runs the command. -->
      <template v-if="desktop">
        <div class="t-title mt-3 mb-1">{{ t('account.devices.thisComputer') }}</div>
        <div class="t-caption c-muted mb-3">{{ t('account.devices.thisComputerHint') }}</div>
        <BaseButton kind="primary" :loading="connecting" @click="$emit('connect-this')">
          {{ t('account.devices.connectThis') }}
        </BaseButton>
        <div v-if="connecting" class="t-caption c-muted mt-2">{{ connectStep }}</div>
        <div v-if="connectError" class="t-caption c-danger mt-2">{{ connectError }}</div>
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
          <BaseButton kind="ghost" size="sm" prepend-icon="mdi-content-copy" @click="$emit('copy', c.command)">
            {{ t('account.devices.copy') }}
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
          <DeviceLiveViewer :sid="liveScreen.sid" :make-url="makeUrl" />
        </div>
      </v-card>
    </v-dialog>

    <!-- Ask before unlinking: an in-app dialog, not the browser's native confirm(). -->
    <ConfirmDialog
      :model-value="unbindOpen"
      :title="unbindTitle"
      :confirm-label="t('account.devices.unbind')"
      danger
      :loading="unbinding"
      @update:model-value="$emit('update:unbind-open', $event)"
      @confirm="$emit('confirm-unbind')"
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
