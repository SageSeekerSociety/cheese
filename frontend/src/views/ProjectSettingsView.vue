<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'
import { toast } from 'vuetify-sonner'
import { useQuery } from '@tanstack/vue-query'

import { useBranchProtection } from '@/composables/useBranchProtection'
import { signInWithGithub } from '@/composables/useGithubSignIn'
import { useProjectAddress } from '@/composables/useProjectAddress'
import { useProjectArchive } from '@/composables/useProjectArchive'
import { useProjectComputeSettings } from '@/composables/useProjectComputeSettings'
import { useProjectCreditBudget } from '@/composables/useProjectCreditBudget'
import { useProjectDefaultModelSettings } from '@/composables/useProjectDefaultModelSettings'
import { useProjectEnvironmentSettings } from '@/composables/useProjectEnvironmentSettings'
import { useProjectExport } from '@/composables/useProjectExport'
import { useProjectMcpSettings } from '@/composables/useProjectMcpSettings'
import { useProjectRoles } from '@/composables/useProjectRoles'
import { useProjectSettings } from '@/composables/useProjectSettings'
import { useProjectTaskNamingSettings } from '@/composables/useProjectTaskNamingSettings'
import { provideRevealGate } from '@/composables/useRevealGate'

import { handOverChannel, listChannels, stepIntoChannel } from '@/api/channels'
import { diagnoseRoomEnvironment } from '@/api/environment'
import BaseButton from '@/components/base/BaseButton.vue'
import SettingsOverlay from '@/components/common/SettingsOverlay.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import DeviceSessionsSwitch from '@/components/DeviceSessionsSwitch.vue'
import McpServerDialog from '@/components/McpServerDialog.vue'
import ProjectComputeSettings from '@/components/ProjectComputeSettings.vue'
import ProjectDefaultModelSettings from '@/components/ProjectDefaultModelSettings.vue'
import ProjectEnvironmentSettings from '@/components/ProjectEnvironmentSettings.vue'
import ProjectMcpSettings from '@/components/ProjectMcpSettings.vue'
import ProjectTaskNamingSettings from '@/components/ProjectTaskNamingSettings.vue'
import AgentTeamSettings from '@/components/settings/AgentTeamSettings.vue'
import ArchiveProjectSection from '@/components/settings/ArchiveProjectSection.vue'
import AttributionSettings from '@/components/settings/AttributionSettings.vue'
import BranchProtectionSection from '@/components/settings/BranchProtectionSection.vue'
import CreditsPanel from '@/components/settings/CreditsPanel.vue'
import ForgeRepoStatus from '@/components/settings/ForgeRepoStatus.vue'
import GithubRepoSettings from '@/components/settings/GithubRepoSettings.vue'
import ProjectAddressSection from '@/components/settings/ProjectAddressSection.vue'
import ProjectChannelSettings from '@/components/settings/ProjectChannelSettings.vue'
import ProjectCreditBudget from '@/components/settings/ProjectCreditBudget.vue'
import ProjectExportSection from '@/components/settings/ProjectExportSection.vue'
import ProjectRolesSettings from '@/components/settings/ProjectRolesSettings.vue'
import UpstreamRepoSettings from '@/components/settings/UpstreamRepoSettings.vue'
import { t } from '@/i18n'
import { closeOverlay } from '@/lib/backOut'
import { ownGithub } from '@/lib/githubAccount'
import { mayIn } from '@/lib/projectPermissions'
import { pageBeforeSettings } from '@/lib/settingsReturn'
import { queryClient } from '@/query/client'
import { projectQuery } from '@/query/project'
import { useWorkspaceStore } from '@/stores/workspace'

// 项目设置（`/projects/<id>/settings/<栏>`）。盖在整个窗口上的一层（SettingsOverlay），
// 左边九栏，一次画一栏；取数照旧一次取完。这一页只剩**接线**：取数与保存在
// `composables/useProjectSettings.ts`（仓库那一组）和 `composables/useBranchProtection.ts`
// （分支保护），各块的画法在 `components/settings/*.vue`，谁在哪一组里、哪一块什么时候
// 出现，看下面那个模板就够了。
//
// 分栏是因为这一页的读者一次只为一件事来：换队友 / 调机器 / 定合并规则 / 接仓库。
// 原来十几块竖着铺满一页，读的人得自己认哪块是哪块。
//
// 拆的时候有一条边界值得记下来：页面给子组件的**根元素**盖作用域戳，所以 `.page-section`
// （在根上）照旧由这一页的 scoped 样式画；而各块**正文里**的元素不带这个戳 —— 那两条
// 用在正文里的小字（`.settings-hint` / `.settings-row-label`）因此跟着搬进了
// `components/settings/settings-section.css`，由用得着的那几件各自 `scoped src` 引一份。
//
// The project default never moves an agent that has already started working.
const props = defineProps<{ projectId: string; section?: string }>()

const workspace = useWorkspaceStore()
const project = computed(() => workspace.projects.find((p) => p.id === props.projectId) ?? null)
const projectName = computed(() => project.value?.name ?? '')
// 我在这个项目里能做什么（#3830）：每一栏按它给不给，后端动手时按同一条规则再判一次。
const projectRead = useQuery(
  computed(() => projectQuery(props.projectId)),
  queryClient
)
const ownsProject = computed(() => projectRead.data.value?.my_role === 'owner')
const managesChannels = computed(() => mayIn(projectRead.data.value, 'manage_channels'))
const editsSettings = computed(() => mayIn(projectRead.data.value, 'edit_settings'))
const changesRoles = computed(() => mayIn(projectRead.data.value, 'change_roles'))
const editsCreditBudget = computed(() => ownsProject.value || projectRead.data.value?.my_role === 'admin')

const {
  loading,
  error,
  upstreamUrl,
  savingUpstream,
  forgeConnection,
  attribution,
  attributionSaving,
  attributionError,
  saveAttribution,
  connectingGithubRepo,
  githubRepoNotice,
  githubAccountLoadState,
  githubAccountConn,
  loadGithubAccountConnection,
  load,
  saveUpstream,
  connectGithubRepo,
  consumeGithubCallbackNotice,
} = useProjectSettings(() => props.projectId)

const {
  bpLoadState,
  bpLoadError,
  bp,
  bpError,
  bpSaving,
  newCheckName,
  newCheckPaths,
  approvalsDraft,
  bpMemberItems,
  bpReviewerItems,
  loadBranchProtection,
  saveBranchProtection,
  addRequiredCheck,
  removeRequiredCheck,
  saveApprovals,
  saveOverrideHandles,
} = useBranchProtection(() => props.projectId)

const { exporting, exportProject } = useProjectExport(
  () => props.projectId,
  () => projectName.value
)

const { mdAndUp } = useDisplay()
const router = useRouter()

// 归档成了：项目已经不在清单里，清单刷一遍（刷不成功不该把归档变成失败），人落到
// 「已归档的项目」——刚归档的这个就列在那里，取消归档也在那里。不回首页：首页会把人
// 带到上次待的另一个项目，看上去像什么都没发生、又像换了个项目。名字先记下来：清单
// 刷过之后这个项目就查不到了。replace：再按回退键不该又落回这个项目的设置页。
const { archiving, archiveError, archiveProject } = useProjectArchive(
  () => props.projectId,
  async () => {
    const name = projectName.value
    await Promise.allSettled([workspace.refreshProjects()])
    toast.success(t('work.projectSettings.archive.done', { name }))
    void router.replace({ name: 'my-archived-projects' })
  }
)

const address = useProjectAddress(() => props.projectId)
const agentName = computed(() => workspace.agentName || t('work.room.defaultAgentName'))

/** 九栏；归档只有所有者看得到。 */
const SECTIONS = computed(() => [
  { group: 'collab', key: 'roles', icon: 'mdi-shield-account-outline' },
  ...(managesChannels.value ? [{ group: 'collab', key: 'channels', icon: 'mdi-pound' }] : []),
  ...(editsSettings.value ? [{ group: 'collab', key: 'address', icon: 'mdi-link-variant' }] : []),
  { group: 'ai', key: 'agents', icon: 'mdi-robot-outline' },
  { group: 'ai', key: 'task-naming', icon: 'mdi-format-title' },
  { group: 'run', key: 'environment', icon: 'mdi-console' },
  { group: 'code', key: 'merge', icon: 'mdi-source-merge' },
  { group: 'code', key: 'repository', icon: 'mdi-source-repository' },
  { group: 'code', key: 'mcp', icon: 'mdi-connection' },
  { group: 'data', key: 'export', icon: 'mdi-download-outline' },
  ...(ownsProject.value ? [{ group: 'danger', key: 'archive', icon: 'mdi-archive-outline', danger: true }] : []),
])

const groups = computed(() =>
  ['collab', 'ai', 'run', 'code', 'data', 'danger']
    .map((group) => ({
      key: group,
      title: group === 'danger' ? undefined : t(`work.projectSettings.groups.${group}`),
      items: SECTIONS.value
        .filter((s) => s.group === group)
        .map((s) => ({
          key: s.key,
          label: t(`work.projectSettings.sections.${s.key}`),
          icon: s.icon,
          danger: 'danger' in s ? s.danger : undefined,
          to: { name: 'project-settings', params: { projectId: props.projectId, section: s.key } },
        })),
    }))
    .filter((group) => group.items.length)
)

// GitHub 授权回来时落在 `/settings?github_install=…` 上（地址由后端给，不带栏）：
// 那条提示在「仓库与署名」里，就落到那一栏。开页那一刻认一次，之后提示读完、地址上
// 的参数被清掉，也不会跳回第一栏。
const query = router.currentRoute.value.query
const landing = query.github_install ? 'repository' : 'agents'

/** 当前这一栏。地址上没写栏时：桌面落到第一栏（GitHub 回来时落到仓库），手机上是目录。 */
const section = computed(() => {
  if (props.section && SECTIONS.value.some((s) => s.key === props.section)) return props.section
  return mdAndUp.value || landing === 'repository' ? landing : null
})

// 设置页的显示闸：各块首次取数期间占住，全部到齐一起出现（见 useRevealGate）。
// 同一 setup 的 composable 显式接收 gate；provide 留给子组件。
const gate = provideRevealGate()
const { revealed } = gate

const creditBudget = useProjectCreditBudget(() => props.projectId, {
  enabled: () => section.value === 'environment',
})
const roles = useProjectRoles(() => props.projectId, {
  enabled: () => section.value === 'roles',
})

function syncActiveSectionLoads() {
  if (section.value === 'repository') {
    void load()
    void loadGithubAccountConnection().finally(gate.hold())
  } else if (section.value === 'merge') {
    void loadBranchProtection().finally(gate.hold())
  }
}

onMounted(() => {
  consumeGithubCallbackNotice()
  syncActiveSectionLoads()
})
watch([() => props.projectId, () => section.value], () => {
  syncActiveSectionLoads()
})

// 这五栏的数据与动作。组件只吃 props、只往上发事件；取数、保存、错误、busy / 显示闸、
// 环境状态轮询与重试/采用、MCP 授权回跳都在各自那一个 composable 里。`enabled` 说的是
// 「这一刻是不是这一栏」：设置页一次只画一栏，没被打开过的那几栏不读，环境那一栏不起
// 轮询的表（原来那五件只有被画出来才会挂上、才会取数）。
const taskNaming = useProjectTaskNamingSettings(() => props.projectId, {
  revealGate: gate,
  enabled: () => section.value === 'task-naming',
})
const compute = useProjectComputeSettings(() => props.projectId, {
  revealGate: gate,
  enabled: () => section.value === 'environment',
})
const defaultModel = useProjectDefaultModelSettings(() => props.projectId, {
  revealGate: gate,
  enabled: () => section.value === 'agents',
})
const environment = useProjectEnvironmentSettings(() => props.projectId, {
  revealGate: gate,
  enabled: () => section.value === 'environment',
  room: () => (typeof query.room === 'string' ? query.room : undefined),
  diagnoseRoom: (roomId) => diagnoseRoomEnvironment(props.projectId, roomId),
})
const configuresAgents = computed(() => mayIn(projectRead.data.value, 'configure_agents'))
const mcp = useProjectMcpSettings(() => props.projectId, {
  agentName: () => agentName.value,
  revealGate: gate,
  enabled: () => section.value === 'mcp',
})

const sectionLabel = computed(() => (section.value ? t(`work.projectSettings.sections.${section.value}`) : ''))

function close() {
  closeOverlay(router, pageBeforeSettings({ name: 'workspace-project', params: { projectId: props.projectId } }))
}
</script>

<template>
  <SettingsOverlay
    :label="t('work.projectSettings.title')"
    :groups="groups"
    :active="section"
    :index-to="{ name: 'project-settings', params: { projectId } }"
    :close-label="t('work.projectSettings.close')"
    :close-title="t('work.projectSettings.closeHint')"
    :back-label="t('work.projectSettings.back')"
    @close="close"
  >
    <!-- Deliberately not an AppPage: this is a settings overlay
         (components/common/SettingsOverlay, meta.settingsOverlay) — it brings
         its own 264px index and a 720px centred content column, the same frame
         the personal and space settings share, and the page name is written in
         that content, so there is no AppPage header to add
         (docs/design-system.md §3.5). -->

    <template #head>
      <div class="whose">
        <UserAvatar :name="projectName" size="32" kind="org" />
        <div class="whose__text">
          <span class="whose__name">{{ projectName }}</span>
          <span class="whose__sub">{{ t('work.projectSettings.title') }}</span>
        </div>
      </div>
    </template>

    <div class="settings-page-body">
      <header class="settings-head">
        <h1 class="t-page-title">{{ sectionLabel }}</h1>
        <!-- 队友可以从市场里挑：这一颗原来在整页的页头上，拆开后跟着队友那一栏。 -->
        <BaseButton
          v-if="section === 'agents'"
          kind="secondary"
          size="sm"
          prepend-icon="mdi-storefront-outline"
          :to="{ name: 'market' }"
        >
          {{ t('work.projectSettings.market') }}
        </BaseButton>
      </header>

      <div v-if="section === 'repository' && loading" class="d-flex justify-center py-10">
        <v-progress-circular indeterminate color="primary" />
      </div>
      <v-alert v-else-if="section === 'repository' && error" type="error" density="comfortable" class="mb-4">
        {{ error }}
      </v-alert>
      <!-- 各块各自取数；全部首次到齐之前藏在一个转圈后面，到齐后一起出现，不然每到
           一块就把下面的往下推一次。见 useRevealGate。一次只画一栏，但取数照旧一次取完：
           在栏之间切换不再等。 -->
      <div v-else class="reveal-gate settings-body" :class="{ 'reveal-gate--waiting': !revealed }">
        <template v-if="section === 'agents'">
          <AgentTeamSettings :project-id="projectId" />
          <section class="page-section">
            <div class="page-section-head">
              <v-icon size="14" class="c-faint">mdi-brain</v-icon>
              <span class="page-section-title">{{ t('work.projectSettings.defaultModel') }}</span>
            </div>
            <div class="page-section-body">
              <ProjectDefaultModelSettings
                :state="defaultModel.state.value"
                :load-error="defaultModel.loadError.value"
                :saving="defaultModel.saving.value"
                :saved="defaultModel.saved.value"
                :save-error="defaultModel.saveError.value"
                @save="defaultModel.save"
              />
            </div>
          </section>
        </template>

        <ProjectRolesSettings
          v-else-if="section === 'roles'"
          :grants="roles.grants.value"
          :editable="changesRoles"
          :agent-name="agentName"
          :is-default="roles.isDefault.value"
          :error="roles.error.value"
          @toggle="roles.toggle"
          @reset="roles.reset"
        />
        <ProjectChannelSettings
          v-else-if="section === 'channels'"
          :project-id="projectId"
          :members="workspace.members"
          :load="listChannels"
          :step-in="stepIntoChannel"
          :hand-over="handOverChannel"
          @open-channel="(id) => router.push({ name: 'workspace-topic', params: { projectId, topicId: id } })"
        />
        <section v-else-if="section === 'task-naming'" class="page-section">
          <ProjectTaskNamingSettings
            :state="taskNaming.state.value"
            :error="taskNaming.error.value"
            :busy="taskNaming.busy.value"
            @choose="taskNaming.choose"
          />
        </section>

        <!-- 环境：在哪运行、怎么准备、还能跑多久，一页说完。环境出了问题跳到这里，
             要改的都在眼前。 -->
        <template v-else-if="section === 'environment'">
          <section class="page-section">
            <div class="page-section-head">
              <v-icon size="14" class="c-faint">mdi-server-outline</v-icon>
              <span class="page-section-title">{{ t('work.projectSettings.defaultComputer') }}</span>
            </div>
            <div class="page-section-body">
              <ProjectComputeSettings
                :state="compute.state.value"
                :error="compute.error.value"
                :busy="compute.busy.value"
                :editing="compute.editing.value"
                :project-id="projectId"
                :device-sessions-component="DeviceSessionsSwitch"
                @changed="compute.load"
                @toggle="compute.toggleEditing"
                @save="compute.save"
              />
            </div>
          </section>
          <ProjectEnvironmentSettings
            :info="environment.info.value"
            :status="environment.status.value"
            :selected-room="environment.selectedRoom.value"
            :error="environment.error.value"
            :saving="environment.saving.value"
            :saved="environment.saved.value"
            :save-error="environment.saveError.value"
            :applying="environment.applying.value"
            :diagnosis="environment.diagnosis.value"
            :diagnosing="environment.diagnosing.value"
            :agent-name="workspace.agentName"
            @reload="environment.load"
            @select-room="environment.selectRoom"
            @refresh-status="environment.refreshStatus"
            @save="environment.save"
            @apply="environment.apply"
            @diagnose="environment.diagnose"
            @retry="environment.retry"
            @adopt="environment.adopt"
            @dismiss="environment.dismissDiagnosis"
          />
          <CreditsPanel :project-id="projectId" />
          <ProjectCreditBudget
            :budget="creditBudget.budget.value"
            :editable="editsCreditBudget"
            :loading="creditBudget.loading.value"
            :saving="creditBudget.saving.value"
            :error="creditBudget.error.value"
            @save="creditBudget.save"
            @reload="creditBudget.load"
          />
        </template>

        <!-- 分支保护 (#718): 平台侧的合并规则，照 GitHub 分支保护那一页的顺序。 -->
        <BranchProtectionSection
          v-else-if="section === 'merge'"
          :state="bpLoadState"
          :load-error="bpLoadError"
          :bp="bp"
          :saving="bpSaving"
          :error="bpError"
          :member-items="bpMemberItems"
          :reviewer-items="bpReviewerItems"
          :check-name="newCheckName"
          :check-paths="newCheckPaths"
          :approvals-draft="approvalsDraft"
          @retry="loadBranchProtection"
          @save="saveBranchProtection"
          @add-check="addRequiredCheck"
          @remove-check="removeRequiredCheck"
          @save-approvals="saveApprovals"
          @save-override="saveOverrideHandles"
          @update:check-name="newCheckName = $event"
          @update:check-paths="newCheckPaths = $event"
          @update:approvals-draft="approvalsDraft = $event"
          @clear-error="bpError = null"
        />

        <template v-else-if="section === 'repository'">
          <!-- 上游仓库 (spec §6.3): link an existing repo, keep pulling it in.
               只在还没接上仓库的 GitHub 项目里出现，所以这个条件留在这里。 -->
          <UpstreamRepoSettings
            v-if="forgeConnection?.kind === 'github_app' && !forgeConnection.connected"
            :url="upstreamUrl"
            :saving="savingUpstream"
            @update:url="upstreamUrl = $event"
            @save="saveUpstream"
          />
          <ForgeRepoStatus v-if="forgeConnection?.kind === 'forgejo'" :forge="forgeConnection" />
          <GithubRepoSettings
            v-if="forgeConnection?.kind === 'github_app'"
            :forge="forgeConnection"
            :connecting="connectingGithubRepo"
            :notice="githubRepoNotice"
            @connect="connectGithubRepo"
            @clear-notice="githubRepoNotice = null"
          />
          <AttributionSettings
            :mode="attribution?.mode ?? null"
            :agent-name="agentName"
            :saving="attributionSaving"
            :error="attributionError"
            :github="ownGithub(githubAccountConn, githubAccountLoadState === 'loaded')"
            manage-to="/users/settings/security"
            @save="saveAttribution"
            @sign-in-github="signInWithGithub"
          />
        </template>

        <ProjectAddressSection
          v-else-if="section === 'address'"
          :slug="address.slug.value"
          :prefix="address.prefix"
          :saving="address.saving.value"
          :error="address.error.value"
          @save="address.save"
        />

        <ProjectMcpSettings
          v-else-if="section === 'mcp'"
          :list="mcp.list.value"
          :error="mcp.error.value"
          :busy="mcp.busy.value"
          :values="mcp.values"
          :manages="configuresAgents"
          :agent-name="agentName"
          @connect="mcp.connect"
          @disconnect="mcp.disconnect"
          @save="mcp.save"
          @clear="mcp.clear"
          @update:value="({ name, value }) => mcp.setValue(name, value)"
          @add="mcp.openAdd"
          @edit="mcp.openEdit"
          @remove="mcp.remove"
        />
        <McpServerDialog
          v-if="section === 'mcp'"
          v-model="mcp.form.open"
          :initial="mcp.editing.value"
          :agent-name="agentName"
          :saving="mcp.form.saving"
          :error="mcp.form.error"
          @save="mcp.submit"
        />

        <!-- Exporting is for everyone who can read the project; the archive follows the
             caller's current room permissions (docs/project-export.md). -->
        <ProjectExportSection v-else-if="section === 'export'" :busy="exporting" @export="exportProject" />

        <!-- 归档只给所有者：归档是他一个人的决定（后端也只认他）。 -->
        <ArchiveProjectSection
          v-else-if="section === 'archive' && ownsProject"
          :project-name="projectName"
          :archiving="archiving"
          :error="archiveError"
          @archive="archiveProject"
        />
      </div>
    </div>
  </SettingsOverlay>
</template>

<style scoped>
/* 这一页各块的窄屏排法按内容列有多宽决定，不按窗口（docs/design-system.md §3.5）：
   子组件（队友、环境变量）里的 @container 也量的是这一格。
   宽度和水平内距不在这里：这一页也住在浮层那一条内容列里（SettingsOverlay 的
   `.so__content`，720 居中），四类设置页共用同一条，只留这一页自己的竖向节奏。 */
.settings-page-body {
  display: flex;
  flex-direction: column;
  gap: 20px;
  padding: 24px 0 48px;
}
.settings-head {
  display: flex;
  gap: 16px;
  align-items: center;
  justify-content: space-between;
}
/* 每一块是一张卡片：细线描边、白底、不加阴影，和个人设置、空间设置同一个样子。
   块标题是卡片标题。标题行、标题、正文三条用 :deep()，因为队友、工作电脑、额度那几块
   是子组件自己画的区块头，只写 scoped 的话样式到不了它们里面。子组件的根元素带着这一页
   的作用域戳，所以 `.page-section` 本身不用 :deep()。 */
.settings-body {
  display: flex;
  flex-direction: column;
  gap: 20px;
  container-type: inline-size;
}
.page-section {
  margin: 0;
  padding: 20px 24px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--surface);
}
:deep(.page-section-head) {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}
:deep(.page-section-title) {
  color: var(--ink);
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
}
:deep(.page-section-body) {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
/* 队友那一块里每个队友原本自己是一张卡；放进卡片里就成了卡中卡，改成用细线分开的行。 */
.agent-team :deep(.v-card) {
  margin-bottom: 0 !important;
  padding: 14px 0 !important;
  border: 0 !important;
  border-top: 1px solid var(--line) !important;
  border-radius: 0 !important;
  background: transparent;
}
.whose {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 0 10px 4px;
}
.whose__text {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.whose__name {
  overflow: hidden;
  color: var(--ink);
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.whose__sub {
  color: var(--faint);
  font-size: 12px;
  line-height: var(--lh-12);
}
/* 断点对齐共享 token（`styles/breakpoints.scss`）：599.98 → 767.98，和设置页一起
   加载的 `settings-card.css` 同一条线。 */
@media (max-width: 767.98px) {
  .settings-page-body {
    padding: 16px 0 32px;
  }
  .page-section {
    padding: 16px;
  }
}
</style>
