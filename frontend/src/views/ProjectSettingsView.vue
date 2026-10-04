<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'

import { useBranchProtection } from '@/composables/useBranchProtection'
import { useProjectSettings } from '@/composables/useProjectSettings'
import { provideRevealGate } from '@/composables/useRevealGate'

import BaseButton from '@/components/base/BaseButton.vue'
import SettingsOverlay from '@/components/common/SettingsOverlay.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import ProjectComputeSettings from '@/components/ProjectComputeSettings.vue'
import ProjectDefaultModelSettings from '@/components/ProjectDefaultModelSettings.vue'
import ProjectEnvironmentSettings from '@/components/ProjectEnvironmentSettings.vue'
import ProjectMcpSettings from '@/components/ProjectMcpSettings.vue'
import ProjectTopicNamingSettings from '@/components/ProjectTopicNamingSettings.vue'
import AgentTeamSettings from '@/components/settings/AgentTeamSettings.vue'
import ArchiveProjectSection from '@/components/settings/ArchiveProjectSection.vue'
import AttributionSettings from '@/components/settings/AttributionSettings.vue'
import BranchProtectionSection from '@/components/settings/BranchProtectionSection.vue'
import CreditsPanel from '@/components/settings/CreditsPanel.vue'
import ForgeRepoStatus from '@/components/settings/ForgeRepoStatus.vue'
import GithubAccountSettings from '@/components/settings/GithubAccountSettings.vue'
import GithubRepoSettings from '@/components/settings/GithubRepoSettings.vue'
import UpstreamRepoSettings from '@/components/settings/UpstreamRepoSettings.vue'
import { t } from '@/i18n'
import { closeOverlay } from '@/lib/backOut'
import { pageBeforeSettings } from '@/lib/settingsReturn'
import { myHandle } from '@/me'
import { useWorkspaceStore } from '@/stores/workspace'

// 项目设置（`/projects/<id>/settings/<栏>`）。盖在整个窗口上的一层（SettingsOverlay），
// 左边八栏，一次画一栏；取数照旧一次取完。这一页只剩**接线**：取数与保存在
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
const ownsProject = computed(() => !!project.value?.owner_handle && project.value.owner_handle === myHandle())

const {
  loading,
  error,
  upstreamUrl,
  savingUpstream,
  forgeConnection,
  attribution,
  attributionSaving,
  attributionError,
  attributionChoice,
  attributionItems,
  saveAttribution,
  connectingGithubRepo,
  connectingGithubAccount,
  githubRepoNotice,
  githubAccountNotice,
  githubAccountLoadState,
  githubAccountLoadError,
  githubAccountConn,
  disconnectingGithubAccount,
  loadGithubAccountConnection,
  disconnectGithubAccount,
  load,
  saveUpstream,
  connectGithubRepo,
  connectGithubAccount,
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

const gate = provideRevealGate()
const { revealed } = gate

onMounted(() => {
  consumeGithubCallbackNotice()
  load()
  void loadBranchProtection().finally(gate.hold())
  void loadGithubAccountConnection().finally(gate.hold())
})
watch(
  () => props.projectId,
  () => {
    load()
    loadBranchProtection()
  }
)
const { mdAndUp } = useDisplay()
const router = useRouter()

/** 八栏；归档只有所有者看得到。 */
const SECTIONS = computed(() => [
  { group: 'ai', key: 'agents', icon: 'mdi-robot-outline' },
  { group: 'ai', key: 'topic-naming', icon: 'mdi-format-title' },
  { group: 'run', key: 'computer', icon: 'mdi-server-outline' },
  { group: 'run', key: 'environment', icon: 'mdi-console' },
  { group: 'code', key: 'merge', icon: 'mdi-source-merge' },
  { group: 'code', key: 'repository', icon: 'mdi-source-repository' },
  { group: 'code', key: 'mcp', icon: 'mdi-connection' },
  ...(ownsProject.value ? [{ group: 'danger', key: 'archive', icon: 'mdi-archive-outline', danger: true }] : []),
])

const groups = computed(() =>
  ['ai', 'run', 'code', 'danger']
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
const landing = query.github_install || query.github_account ? 'repository' : 'agents'

/** 当前这一栏。地址上没写栏时：桌面落到第一栏（GitHub 回来时落到仓库），手机上是目录。 */
const section = computed(() => {
  if (props.section && SECTIONS.value.some((s) => s.key === props.section)) return props.section
  return mdAndUp.value || landing === 'repository' ? landing : null
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

      <div v-if="loading" class="d-flex justify-center py-10">
        <v-progress-circular indeterminate color="primary" />
      </div>
      <v-alert v-else-if="error" type="error" density="comfortable" class="mb-4">
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
              <ProjectDefaultModelSettings :project-id="projectId" />
            </div>
          </section>
        </template>

        <section v-else-if="section === 'topic-naming'" class="page-section">
          <ProjectTopicNamingSettings :project-id="projectId" />
        </section>

        <template v-else-if="section === 'computer'">
          <section class="page-section">
            <div class="page-section-head">
              <v-icon size="14" class="c-faint">mdi-server-outline</v-icon>
              <span class="page-section-title">{{ t('work.projectSettings.defaultComputer') }}</span>
            </div>
            <div class="page-section-body">
              <ProjectComputeSettings :project-id="projectId" />
            </div>
          </section>
          <CreditsPanel :project-id="projectId" />
        </template>

        <ProjectEnvironmentSettings v-else-if="section === 'environment'" :project-id="projectId" />

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
            :choice="attributionChoice"
            :items="attributionItems"
            :saving="attributionSaving"
            :error="attributionError"
            :effective="attribution?.effective ?? false"
            @save="saveAttribution"
          />
          <GithubAccountSettings
            :state="githubAccountLoadState"
            :load-error="githubAccountLoadError"
            :conn="githubAccountConn"
            :connecting="connectingGithubAccount"
            :disconnecting="disconnectingGithubAccount"
            :notice="githubAccountNotice"
            @retry="loadGithubAccountConnection"
            @connect="connectGithubAccount"
            @disconnect="disconnectGithubAccount"
            @clear-notice="githubAccountNotice = null"
          />
        </template>

        <ProjectMcpSettings v-else-if="section === 'mcp'" :project-id="projectId" />

        <!-- 归档只给所有者：归档是他一个人的决定（后端也只认他）。 -->
        <ArchiveProjectSection
          v-else-if="section === 'archive' && ownsProject"
          :project-id="projectId"
          :project-name="projectName"
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
