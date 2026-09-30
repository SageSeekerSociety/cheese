<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'

import { useBranchProtection } from '@/composables/useBranchProtection'
import { useProjectSettings } from '@/composables/useProjectSettings'
import { provideRevealGate } from '@/composables/useRevealGate'

import { useCommands } from '@/commands'
import AppPage from '@/components/common/AppPage.vue'
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
import { myHandle } from '@/me'
import { useWorkspaceStore } from '@/stores/workspace'

// 项目设置（`/projects/<id>/settings`）。这一页现在只剩**接线**：取数与保存在
// `composables/useProjectSettings.ts`（仓库那一组）和 `composables/useBranchProtection.ts`
// （分支保护），各块的画法在 `components/settings/*.vue`，谁在哪一组里、哪一块什么时候
// 出现，看下面那个模板就够了。
//
// 分四组（所有者多一组「归档」），因为这一页的读者一次只为一件事来：换队友 / 调机器 /
// 定交付规则 / 接仓库。原来是六块竖着铺满一页，读的人得自己认哪块是哪块；而没绑仓库
// 的项目从头到尾只看得到跟仓库有关的东西，于是整页像是坏的。拆开之前这一页 1041 行。
//
// 拆的时候有一条边界值得记下来：页面给子组件的**根元素**盖作用域戳，所以 `.page-section`
// （在根上）照旧由这一页的 scoped 样式画；而各块**正文里**的元素不带这个戳 —— 那两条
// 用在正文里的小字（`.settings-hint` / `.settings-row-label`）因此跟着搬进了
// `components/settings/settings-section.css`，由用得着的那几件各自 `scoped src` 引一份。
//
// The project default never moves an agent that has already started working.
const props = defineProps<{ projectId: string }>()

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
useCommands(() => [
  {
    id: 'settings.market',
    title: '市场',
    icon: 'mdi-storefront-outline',
    header: { primary: true },
    to: { name: 'market' },
  },
])
</script>

<template>
  <AppPage :title="t('navigation.project.settings')">
    <div v-if="loading" class="d-flex justify-center py-10">
      <v-progress-circular indeterminate color="primary" />
    </div>
    <v-alert v-else-if="error" type="error" density="comfortable" class="mb-4">
      {{ error }}
    </v-alert>

    <!-- 下面各块各自取数；全部首次到齐之前整页藏在一个转圈后面，到齐后一起出现，
         不然每到一块就把下面的往下推一次。见 useRevealGate。 -->
    <div v-else class="reveal-gate settings-body" :class="{ 'reveal-gate--waiting': !revealed }">
      <!-- 分四组，因为这一页的读者一次只为一件事来：换队友 / 调机器 / 定交付
             规则 / 接仓库。原来是六块竖着铺满一页，读的人得自己认哪块是哪块；而
             没绑仓库的项目从头到尾只看得到跟仓库有关的东西，于是整页像是坏的。 -->
      <h2 class="t-title settings-group">队友</h2>
      <AgentTeamSettings :project-id="projectId" />

      <section class="page-section">
        <div class="page-section-head">
          <v-icon size="14" class="c-faint">mdi-brain</v-icon>
          <span class="page-section-title">默认模型</span>
        </div>
        <div class="page-section-body">
          <ProjectDefaultModelSettings :project-id="projectId" />
        </div>
      </section>

      <section class="page-section">
        <div class="page-section-head">
          <v-icon size="14" class="c-faint">mdi-format-title</v-icon>
          <span class="page-section-title">话题命名</span>
        </div>
        <div class="page-section-body">
          <ProjectTopicNamingSettings :project-id="projectId" />
        </div>
      </section>

      <h2 class="t-title settings-group">工作电脑</h2>
      <section class="page-section">
        <div class="page-section-head">
          <v-icon size="14" class="c-faint">mdi-server-outline</v-icon>
          <span class="page-section-title">默认工作电脑</span>
        </div>
        <div class="page-section-body">
          <ProjectComputeSettings :project-id="projectId" />
        </div>
      </section>
      <ProjectEnvironmentSettings :project-id="projectId" />
      <CreditsPanel :project-id="projectId" />

      <h2 class="t-title settings-group">交付</h2>

      <!-- 分支保护 (#718): 平台侧的合并规则，照 GitHub 分支保护那一页的顺序。 -->
      <BranchProtectionSection
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

      <h2 class="t-title settings-group">仓库</h2>

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

      <ProjectMcpSettings :project-id="projectId" />

      <!-- 最后一组只给所有者：归档是他一个人的决定（后端也只认他）。 -->
      <template v-if="ownsProject">
        <h2 class="t-title settings-group">归档</h2>
        <ArchiveProjectSection :project-id="projectId" :project-name="projectName" />
      </template>
      <div v-if="!revealed" class="reveal-gate__wait">
        <v-progress-circular indeterminate color="primary" />
      </div>
    </div>
  </AppPage>
</template>

<style scoped>
/* 这一页各块的窄屏排法按内容列有多宽决定，不按窗口（docs/design-system.md §3.5）：
   子组件（队友、环境变量）里的 @container 也量的是这一格。 */
.settings-body {
  container-type: inline-size;
}
/* 区块节奏。区块不是卡片：区块标题是 eyebrow，划分靠留白加一条顶部发丝线。
   标题行、标题、正文三条用 :deep()，因为队友、工作电脑、额度那几块是子组件自己画
   的区块头，只写 scoped 的话样式到不了它们里面，标题就按浏览器默认的 16px 画，
   比上面那一级组标题还大。 */
.page-section {
  padding-top: 18px;
  margin-bottom: 22px;
  border-top: 1px solid var(--line);
}
:deep(.page-section-head) {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 10px;
}
/* 组标题：比区块标题重一档，前后留白把这一页切成四段读得出来的东西。第一组不
   留上边距——它紧接着页头。 */
.settings-group {
  margin: 32px 0 12px;
  color: var(--ink);
}
.settings-group:first-of-type {
  margin-top: 0;
}
:deep(.page-section-title) {
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.04em;
  color: var(--faint);
}
:deep(.page-section-body) {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
/* 选中态自己有底色，hover 不该把它冲淡。 */
.link {
  color: rgb(var(--v-theme-primary));
  cursor: pointer;
  text-decoration: underline;
  text-underline-offset: 2px;
}
</style>
