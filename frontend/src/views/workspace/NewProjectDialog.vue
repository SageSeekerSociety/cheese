<template>
  <!-- 新建项目 (opened by the rail's "+" affordance)。要填好几项，手机上是整页：
       下一步 / 创建在页头右边，键盘弹起来也够得着。 -->
  <AdaptiveDialog
    v-model="newProjectDialog"
    :title="newProjectStep === 1 ? t('work.newProject.title') : t('work.teammate.title')"
    :primary-label="newProjectStep === 1 ? t('work.teammate.next') : t('work.teammate.create')"
    :primary-loading="creatingProject"
    :primary-disabled="
      !newProjectName.trim() ||
      loadingTeams ||
      newProjectTeamId === null ||
      !!teamLoadError ||
      (newProjectStep === 2 && !newProjectAgentName.trim())
    "
    :cancel-label="t('work.newProject.cancel')"
    :close-disabled="creatingProject"
    :max-width="420"
    persistent
    @primary="newProjectStep === 1 ? advanceNewProject() : confirmNewProject()"
  >
    <div v-show="newProjectStep === 1">
      <!-- Three lines of quota detail sat right under the floating label of the
           field below. The one line that changes what a project can expect
           stays; the rest moves behind the notice's own disclosure, and the
           gap below clears the label. -->
      <ResourceLimitsNotice v-if="newProjectDialog" class="mt-3 mb-6" />
      <!-- eslint-disable vuejs-accessibility/no-autofocus -- the person opened this dialog to type the name; focus lands in the field they came for -->
      <v-text-field
        v-model="newProjectName"
        autocomplete="off"
        :label="t('work.newProject.name')"
        variant="outlined"
        color="primary"
        autofocus
        hide-details
        :disabled="creatingProject"
        @keyup.enter="advanceNewProject"
      />
      <!-- eslint-enable vuejs-accessibility/no-autofocus -->
      <!-- 可选：答案会跟着项目进房间（见 ProjectService.create）。不填也能建，
           所以这不是必填项，标签里就写着「可选」。 -->
      <v-textarea
        v-model="newProjectIntent"
        autocomplete="off"
        :label="t('work.newProject.intent')"
        :placeholder="t('work.newProject.intentExample')"
        variant="outlined"
        color="primary"
        rows="2"
        auto-grow
        hide-details
        class="mt-3"
        :disabled="creatingProject"
      />
      <v-select
        v-model="newProjectTeamId"
        autocomplete="off"
        :items="newProjectTeams"
        item-title="name"
        item-value="id"
        :item-props="teamItemProps"
        :label="t('work.newProject.team')"
        variant="outlined"
        color="primary"
        class="mt-3"
        hide-details
        :loading="loadingTeams"
        :disabled="creatingProject || loadingTeams"
      />
      <v-select
        v-model="newProjectForgeKind"
        autocomplete="off"
        :items="[
          { title: t('work.newProject.forgeHosted'), value: 'forgejo' },
          { title: t('work.newProject.forgeGithub'), value: 'github_app' },
        ]"
        :label="t('work.newProject.forge')"
        variant="outlined"
        color="primary"
        class="mt-3"
        hide-details
        :disabled="creatingProject"
      />
      <div class="t-meta-read mt-2">
        {{ newProjectForgeKind === 'forgejo' ? t('work.newProject.forgeHint') : t('work.newProject.forgeGithubHint') }}
      </div>
      <v-alert v-if="teamLoadError" type="error" density="compact" variant="tonal" class="mt-3">
        {{ teamLoadError }}
        <BaseButton kind="secondary" size="sm" :loading="loadingTeams" @click="loadProjectTeams">{{
          t('work.newProject.retry')
        }}</BaseButton>
      </v-alert>
    </div>
    <div v-if="newProjectStep === 2">
      <p class="t-body mb-4">{{ t('work.teammate.intro', { project: newProjectName.trim() }) }}</p>
      <v-text-field
        v-model="newProjectAgentName"
        :label="t('work.teammate.name')"
        variant="outlined"
        autocomplete="off"
        maxlength="64"
        :disabled="creatingProject"
        @keyup.enter="confirmNewProject"
      >
        <template #append-inner>
          <BaseButton
            icon="mdi-dice-multiple-outline"
            size="sm"
            :aria-label="t('work.teammate.random')"
            :title="t('work.teammate.random')"
            :disabled="creatingProject"
            @click="newProjectAgentName = randomTeammateName(newProjectAgentName)"
          />
        </template>
      </v-text-field>
      <p class="t-meta-read">{{ t('work.teammate.more') }}</p>
    </div>
    <v-alert v-if="newProjectError" type="error" density="compact" variant="tonal" class="mt-3">
      {{ newProjectError }}
    </v-alert>
    <template v-if="newProjectStep === 2" #actions>
      <BaseButton kind="ghost" :disabled="creatingProject" @click="newProjectStep = 1">{{
        t('work.teammate.back')
      }}</BaseButton>
    </template>
  </AdaptiveDialog>
</template>

<script setup lang="ts">
// 新建项目：rail 上的「＋」、团队页的「新建项目」、从一道题建项目，都打开这一个
// （useNewProjectDialog 管开关和带进来的团队、题目）。确认后以当前用户的名义建项目，
// 刷新 rail 让新格子出来，再进它的工作区。
import type { Team } from '@/types/teams'

import { defineAsyncComponent, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { defaultTeamFor, useNewProjectDialog } from '@/composables/useNewProjectDialog'

import { createProject } from '@/api'
import { TeamsApi } from '@/api/teams'
import BaseButton from '@/components/base/BaseButton.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import { t } from '@/i18n'
import { randomTeammateName } from '@/lib/projectAgents'
import { refreshProjects } from '@/query/projects'

defineOptions({ name: 'NewProjectDialog' })

// 只在对话框打开时才用得上，不跟外壳一起下。
const ResourceLimitsNotice = defineAsyncComponent(() => import('@/components/ResourceLimitsNotice.vue'))

const router = useRouter()
const { open: newProjectDialog, presetTeam } = useNewProjectDialog()
const newProjectName = ref('')
const newProjectStep = ref(1)
const newProjectAgentName = ref('')
const newProjectForgeKind = ref<'forgejo' | 'github_app'>('forgejo')
// 这个项目打算做什么——建项目时问的那一句（#946 片 C）。问题只在人愿意答的时候
// 才有价值，所以它是可选的：空着就和从前一样，只建一个空房间。
const newProjectIntent = ref('')
// The project's id, chosen once per opening of the dialog. Pressing 创建项目 again
// after a failure sends the same id, so the server finishes that project rather
// than starting another beside what the failed attempt already made.
const newProjectId = ref('')
const creatingProject = ref(false)
const newProjectError = ref<string | null>(null)
// 归属: which team the project belongs to decides who can see it. Without
// this the rail ＋ always chose the personal team, so a project someone made
// for their team was invisible to the rest of it.
const newProjectTeams = ref<Team[]>([])
const newProjectTeamId = ref<number | null>(null)
const loadingTeams = ref(false)
const teamLoadError = ref<string | null>(null)
// 自己名下以自己的昵称出现（后端给的 name 就是昵称），和团队的区别写在下面那行小字里。
const teamItemProps = (team: Team) => ({
  subtitle: team.personal
    ? t('work.newProject.ownHint')
    : t('work.newProject.teamHint', { n: 1 + (team.admins?.total ?? 0) + (team.members?.total ?? 0) }),
})

async function loadProjectTeams() {
  loadingTeams.value = true
  teamLoadError.value = null
  newProjectTeamId.value = null
  try {
    const { teams } = await TeamsApi.getMyTeams()
    newProjectTeams.value = teams
    if (!teams.length) teamLoadError.value = t('work.newProject.teamsEmpty')
  } catch {
    newProjectTeams.value = []
    teamLoadError.value = t('work.newProject.teamsFailed')
  } finally {
    loadingTeams.value = false
  }
  newProjectTeamId.value = defaultTeamFor(presetTeam.value, newProjectTeams.value)
}

watch(newProjectDialog, (opened) => {
  if (!opened) return
  newProjectName.value = ''
  newProjectStep.value = 1
  newProjectAgentName.value = randomTeammateName()
  newProjectForgeKind.value = 'forgejo'
  // 上一次开的对话框留下的答案不该跟到这一次——那会让第二个项目凭空继承第一个
  // 项目的说明，而人根本没说过。
  newProjectIntent.value = ''
  newProjectId.value = crypto.randomUUID()
  newProjectError.value = null
  void loadProjectTeams()
})

function advanceNewProject() {
  if (newProjectName.value.trim() && !loadingTeams.value && !teamLoadError.value && newProjectTeamId.value !== null)
    newProjectStep.value = 2
}

async function confirmNewProject() {
  if (newProjectStep.value !== 2 || !newProjectAgentName.value.trim()) return
  const name = newProjectName.value.trim()
  if (!name || creatingProject.value || loadingTeams.value || teamLoadError.value || newProjectTeamId.value === null)
    return
  creatingProject.value = true
  newProjectError.value = null
  try {
    const project = await createProject(
      name,
      newProjectTeamId.value,
      newProjectForgeKind.value,
      newProjectIntent.value.trim(),
      newProjectAgentName.value.trim(),
      newProjectId.value
    )
    await refreshProjects()
    newProjectDialog.value = false
    if (newProjectForgeKind.value === 'github_app') {
      router.push(`/projects/${project.id}/settings/repository`)
      return
    }
    // 直接落到大本营，而不是项目地址。一个刚建出来的项目没有任何活，而 /projects
    // 的落点是看板——它此刻是四列空格子，答的是「什么在跑」，对一个还没开始的项目
    // 只有一个答案：没有。人第一眼该看到的是能说话的地方。这里知道它是新的，所以
    // 不用等话题列表回来才推断（WorkspaceEntry 负责那种情况）。
    router.push(
      project.root_topic_id ? `/projects/${project.id}/topics/${project.root_topic_id}` : `/projects/${project.id}`
    )
  } catch (e) {
    // Inline error inside the dialog — not a native alert() chrome.
    newProjectError.value = e instanceof Error ? e.message : t('work.newProject.createFailed')
  } finally {
    creatingProject.value = false
  }
}
</script>
