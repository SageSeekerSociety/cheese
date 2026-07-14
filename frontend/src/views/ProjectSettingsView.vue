<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  createRole,
  getComputeProfiles,
  getExecutionProfiles,
  getProject,
  getSandboxImage,
  getUpstream,
  listRoles,
  setComputeProfile,
  setExecutionProfile,
  setProjectExpertRole,
  setSandboxImage,
  setUpstream,
  syncUpstream,
} from '../api'
import { myHandle } from '../me'
import type {
  ComputeProfiles,
  ExecProfiles,
  ExpertRole,
  SandboxImageInfo,
  UpstreamSyncResult,
} from '../cx_types'

// 项目设置 (design v3): a project picks which resource pools it runs on — an AI
// pool (model/provider) and a compute pool (which machine runs the sandbox).
// Both default to 知是's own pool; a project may switch to any pool available to
// it. The full catalog (incl. pools it can't select yet) lives in the 市场.
const props = defineProps<{ projectId: string }>()
const router = useRouter()

const projectName = ref('')
const ai = ref<ExecProfiles | null>(null)
const compute = ref<ComputeProfiles | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)
const env = ref<SandboxImageInfo | null>(null)
const savingAi = ref<string | null>(null)
const savingCompute = ref<string | null>(null)
// '' is the sentinel for the default-image row (image null); a real image key otherwise.
const savingEnv = ref<string | null>(null)
// 上游仓库: the linked repo URL as edited, plus save/sync state and last result.
const upstreamUrl = ref('')
const upstreamSaved = ref<string | null>(null)
const savingUpstream = ref(false)
const syncing = ref(false)
const syncResult = ref<UpstreamSyncResult | null>(null)

// 专家角色 (spec §8.2): which persona 芝士 loads for this project. The catalog
// merges built-in library roles with custom ones; '' = generic 芝士.
const roles = ref<ExpertRole[]>([])
const roleCurrent = ref('')
const savingRole = ref(false)
// 新建角色 dialog: the Claude Code agents-file fields (frontmatter + body).
const roleDialog = ref(false)
const newRoleName = ref('')
const newRoleTitle = ref('')
const newRoleDescription = ref('')
const newRoleBody = ref('')
const creatingRole = ref(false)
const roleFormError = ref<string | null>(null)

const roleItems = computed(() => [
  { name: '', title: '不设置（通用芝士）', description: '' },
  ...roles.value.map((r) => ({
    name: r.name,
    title: r.builtin ? r.title : `${r.title || r.name}（自定义）`,
    description: r.description,
  })),
])

async function pickRole(name: string | null) {
  const next = name ?? ''
  savingRole.value = true
  try {
    const r = await setProjectExpertRole(props.projectId, next)
    roleCurrent.value = r.current ?? ''
  } catch (e) {
    error.value = e instanceof Error ? e.message : '切换专家角色失败'
  } finally {
    savingRole.value = false
  }
}

async function submitNewRole() {
  roleFormError.value = null
  creatingRole.value = true
  try {
    const created = await createRole({
      name: newRoleName.value.trim(),
      title: newRoleTitle.value.trim(),
      description: newRoleDescription.value.trim(),
      body: newRoleBody.value.trim(),
      created_by: myHandle(),
    })
    // A custom role shadows its built-in namesake — keep one entry per name.
    roles.value = [...roles.value.filter((r) => r.name !== created.name), created]
    roleDialog.value = false
    newRoleName.value = ''
    newRoleTitle.value = ''
    newRoleDescription.value = ''
    newRoleBody.value = ''
    // Creating a role from here means "use it": select it right away.
    await pickRole(created.name)
  } catch (e) {
    roleFormError.value = e instanceof Error ? e.message : '创建角色失败'
  } finally {
    creatingRole.value = false
  }
}

const TIER_LABEL: Record<string, string> = {
  default: '默认',
  included: '包含',
  testing: '内测',
  byo: '自带',
  premium: '增值',
}

async function load() {
  loading.value = true
  error.value = null
  try {
    const [proj, execP, compP, envP, upP, rolesP] = await Promise.all([
      getProject(props.projectId),
      getExecutionProfiles(props.projectId),
      getComputeProfiles(props.projectId),
      getSandboxImage(props.projectId),
      getUpstream(props.projectId),
      listRoles(),
    ])
    projectName.value = proj.name
    ai.value = execP
    compute.value = compP
    env.value = envP
    upstreamSaved.value = upP.url
    upstreamUrl.value = upP.url ?? ''
    roles.value = rolesP.data
    roleCurrent.value = proj.expert_role ?? ''
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载设置失败'
  } finally {
    loading.value = false
  }
}

async function pickAi(name: string) {
  if (!ai.value || ai.value.current === name) return
  savingAi.value = name
  try {
    const r = await setExecutionProfile(props.projectId, name)
    ai.value = { ...ai.value, current: r.current }
  } catch (e) {
    error.value = e instanceof Error ? e.message : '切换 AI 池失败'
  } finally {
    savingAi.value = null
  }
}

async function pickCompute(id: string) {
  if (!compute.value || compute.value.current === id) return
  savingCompute.value = id
  try {
    const r = await setComputeProfile(props.projectId, id)
    compute.value = { ...compute.value, current: r.current }
  } catch (e) {
    error.value = e instanceof Error ? e.message : '切换算力池失败'
  } finally {
    savingCompute.value = null
  }
}

// Pick the env image. image='' means the default (pool base image → current null).
async function pickEnv(image: string) {
  if (!env.value || (env.value.current ?? '') === image) return
  savingEnv.value = image || '__default__'
  try {
    const r = await setSandboxImage(props.projectId, image)
    env.value = { ...env.value, current: r.current }
  } catch (e) {
    error.value = e instanceof Error ? e.message : '切换环境镜像失败'
  } finally {
    savingEnv.value = null
  }
}

// Save (or with an empty field, unlink) the upstream repo URL.
async function saveUpstream() {
  savingUpstream.value = true
  syncResult.value = null
  try {
    const r = await setUpstream(props.projectId, upstreamUrl.value.trim())
    upstreamSaved.value = r.url
    upstreamUrl.value = r.url ?? ''
  } catch (e) {
    error.value = e instanceof Error ? e.message : '保存上游仓库失败'
  } finally {
    savingUpstream.value = false
  }
}

// Pull the upstream's new commits into the project repo (merge; conflicts abort
// cleanly and show up in the result line).
async function doSyncUpstream() {
  syncing.value = true
  syncResult.value = null
  try {
    syncResult.value = await syncUpstream(props.projectId)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '同步上游失败'
  } finally {
    syncing.value = false
  }
}

// Back to wherever you came from (the workspace, via the gear), with an overview
// fallback for a deep link — same pattern as the member page.
function goBack() {
  if (window.history.state?.back != null) router.back()
  else router.push({ name: 'overview', params: { projectId: props.projectId } })
}

onMounted(load)
watch(() => props.projectId, load)
</script>

<template>
  <div class="settings-page fill-height overflow-y-auto">
    <v-container class="py-6" style="max-width: 900px">
      <div class="d-flex align-center mb-4">
        <v-btn
          variant="text"
          size="small"
          prepend-icon="mdi-arrow-left"
          class="px-1"
          @click="goBack"
        >
          返回
        </v-btn>
        <v-spacer />
        <v-btn
          variant="text"
          size="small"
          append-icon="mdi-storefront-outline"
          @click="router.push({ name: 'market' })"
        >
          逛市场
        </v-btn>
      </div>

      <div class="mb-6">
        <div class="t-eyebrow mb-1">项目设置 · {{ projectName }}</div>
        <h1 class="t-page-title">资源池</h1>
        <p class="t-body c-muted mt-1" style="max-width: 640px">
          选择这个项目用哪套 AI 模型、跑在哪套算力上。默认都是知是自己的池，开箱即用；
          需要更强的模型或专属机器，可以在
          <a class="link" @click="router.push({ name: 'market' })">市场</a> 里挑。
        </p>
      </div>

      <div v-if="loading" class="d-flex justify-center py-10">
        <v-progress-circular indeterminate color="primary" />
      </div>
      <v-alert v-else-if="error" type="error" density="comfortable" class="mb-4">
        {{ error }}
      </v-alert>

      <template v-else>
        <!-- 专家角色 (spec §8.2): which persona 芝士 loads for this project -->
        <section class="ln-section">
          <div class="ln-section-head">
            <v-icon size="18" class="me-1 c-muted">mdi-account-school-outline</v-icon>
            <span class="ln-section-title">专家角色</span>
          </div>
          <div class="ln-body">
            <div class="d-flex align-center" style="gap: 8px">
              <v-select
                :model-value="roleCurrent"
                :items="roleItems"
                item-title="title"
                item-value="name"
                density="compact"
                variant="outlined"
                hide-details
                :loading="savingRole"
                :disabled="savingRole"
                style="flex: 1"
                @update:model-value="pickRole"
              >
                <template #item="{ props: itemProps, item }">
                  <v-list-item
                    v-bind="itemProps"
                    :subtitle="item.description || undefined"
                  />
                </template>
              </v-select>
              <v-btn
                size="small"
                variant="tonal"
                prepend-icon="mdi-plus"
                @click="roleDialog = true"
              >
                新建角色
              </v-btn>
            </div>
            <p class="t-body c-faint mt-2" style="font-size: 0.8rem">
              芝士以这个专家身份进驻项目（影响它的口吻和关注点）。平台内置了几个；
              也可以给机构或自己定义新角色。
            </p>
          </div>
        </section>

        <!-- AI 模型池 -->
        <section class="ln-section">
          <div class="ln-section-head">
            <v-icon size="18" class="me-1 c-muted">mdi-brain</v-icon>
            <span class="ln-section-title">AI 模型池</span>
          </div>
          <div class="ln-body">
            <button
              v-for="p in ai?.profiles ?? []"
              :key="p.name"
              type="button"
              class="pool-row"
              :class="{ 'pool-row--active': ai?.current === p.name }"
              :disabled="savingAi !== null"
              @click="pickAi(p.name)"
            >
              <span
                class="pool-radio"
                :class="{ 'pool-radio--on': ai?.current === p.name }"
              />
              <div class="pool-main">
                <div class="pool-title">
                  {{ p.label }}
                  <span class="pool-tier">{{ TIER_LABEL[p.tier] ?? p.tier }}</span>
                </div>
                <div class="pool-sub c-muted">模型 {{ p.model }}</div>
              </div>
              <v-progress-circular
                v-if="savingAi === p.name"
                indeterminate
                size="16"
                width="2"
                color="primary"
              />
              <span v-else-if="ai?.current === p.name" class="pool-current">使用中</span>
            </button>
          </div>
        </section>

        <!-- 算力池 -->
        <section class="ln-section">
          <div class="ln-section-head">
            <v-icon size="18" class="me-1 c-muted">mdi-server</v-icon>
            <span class="ln-section-title">算力池</span>
          </div>
          <div class="ln-body">
            <p class="t-body c-muted mb-2" style="font-size: 0.82rem">
              这是<strong>新话题的默认算力</strong>；每个话题在发第一条消息前，都能在输入栏
              单独切换、之后锁定。「自托管设备」来自小队注册的机器（在小队的「算力」页里加机器）。
            </p>
            <button
              v-for="p in compute?.profiles ?? []"
              :key="p.id"
              type="button"
              class="pool-row"
              :class="{ 'pool-row--active': compute?.current === p.id }"
              :disabled="savingCompute !== null"
              @click="pickCompute(p.id)"
            >
              <span
                class="pool-radio"
                :class="{ 'pool-radio--on': compute?.current === p.id }"
              />
              <div class="pool-main">
                <div class="pool-title">
                  {{ p.label }}
                  <span class="pool-tier">{{ p.price }}</span>
                </div>
                <div class="pool-sub c-muted">{{ p.description }}</div>
              </div>
              <v-progress-circular
                v-if="savingCompute === p.id"
                indeterminate
                size="16"
                width="2"
                color="primary"
              />
              <span v-else-if="compute?.current === p.id" class="pool-current">使用中</span>
            </button>
            <p class="t-body c-faint mt-2" style="font-size: 0.8rem">
              需要远程节点或 GPU？到
              <a class="link" @click="router.push({ name: 'market' })">市场</a>
              申请接入。
            </p>
          </div>
        </section>

        <!-- 环境 (spec §9.1): which sandbox image the agent runs in -->
        <section class="ln-section">
          <div class="ln-section-head">
            <v-icon size="18" class="me-1 c-muted">mdi-package-variant-closed</v-icon>
            <span class="ln-section-title">环境镜像</span>
          </div>
          <div class="ln-body">
            <!-- Default (pool base image) -->
            <button
              type="button"
              class="pool-row"
              :class="{ 'pool-row--active': !env?.current }"
              :disabled="savingEnv !== null"
              @click="pickEnv('')"
            >
              <span class="pool-radio" :class="{ 'pool-radio--on': !env?.current }" />
              <div class="pool-main">
                <div class="pool-title">知是基座（默认）</div>
                <div class="pool-sub c-muted">{{ env?.default }}</div>
              </div>
              <v-progress-circular
                v-if="savingEnv === '__default__'"
                indeterminate
                size="16"
                width="2"
                color="primary"
              />
              <span v-else-if="!env?.current" class="pool-current">使用中</span>
            </button>
            <!-- Curated images (e.g. cheesex-dev for dogfooding on this repo) -->
            <button
              v-for="o in env?.options ?? []"
              :key="o.image"
              type="button"
              class="pool-row"
              :class="{ 'pool-row--active': env?.current === o.image }"
              :disabled="savingEnv !== null"
              @click="pickEnv(o.image)"
            >
              <span
                class="pool-radio"
                :class="{ 'pool-radio--on': env?.current === o.image }"
              />
              <div class="pool-main">
                <div class="pool-title">{{ o.label }}</div>
                <div class="pool-sub c-muted">{{ o.image }}</div>
              </div>
              <v-progress-circular
                v-if="savingEnv === o.image"
                indeterminate
                size="16"
                width="2"
                color="primary"
              />
              <span v-else-if="env?.current === o.image" class="pool-current">使用中</span>
            </button>
            <p class="t-body c-faint mt-2" style="font-size: 0.8rem">
              基座装了 uv / node / git 等通用工具；cheesex-dev 额外预装了本仓库的
              依赖，芝士可以直接在盒子里跑 cheesex 自己的测试（dogfooding）。
            </p>
          </div>
        </section>

        <!-- 上游仓库 (spec §6.3): link an existing repo, keep pulling it in -->
        <section class="ln-section">
          <div class="ln-section-head">
            <v-icon size="18" class="me-1 c-muted">mdi-source-branch-sync</v-icon>
            <span class="ln-section-title">上游仓库</span>
          </div>
          <div class="ln-body">
            <div class="d-flex align-center" style="gap: 8px">
              <v-text-field
                v-model="upstreamUrl"
                density="compact"
                variant="outlined"
                hide-details
                placeholder="https://… 或本机绝对路径（留空 = 取消关联）"
                style="flex: 1"
                @keydown.enter="saveUpstream"
              />
              <v-btn
                size="small"
                variant="tonal"
                :loading="savingUpstream"
                @click="saveUpstream"
              >
                保存
              </v-btn>
              <v-btn
                size="small"
                color="primary"
                variant="flat"
                :disabled="!upstreamSaved"
                :loading="syncing"
                @click="doSyncUpstream"
              >
                同步上游
              </v-btn>
            </div>
            <p
              v-if="syncResult"
              class="t-body mt-2"
              style="font-size: 0.8rem"
              :class="syncResult.synced ? 'c-muted' : 'text-error'"
            >
              <template v-if="syncResult.synced && (syncResult.commits ?? 0) > 0">
                已合入上游 {{ syncResult.commits }} 个提交。
              </template>
              <template v-else-if="syncResult.synced">已是最新，没有新提交。</template>
              <template v-else>同步失败：{{ syncResult.reason }}</template>
            </p>
            <p class="t-body c-faint mt-2" style="font-size: 0.8rem">
              关联一个已有的 git 仓库，把它的历史拉进这个项目；之后随时同步新提交。
              有冲突时会原样中止，不会合一半。
            </p>
          </div>
        </section>
      </template>
    </v-container>

    <!-- 新建角色: the Claude Code agents-file fields — name/title/description
         (frontmatter) + persona body. -->
    <v-dialog v-model="roleDialog" max-width="620">
      <v-card>
        <v-card-title class="pt-4">新建专家角色</v-card-title>
        <v-card-text class="pb-0">
          <v-text-field
            v-model="newRoleName"
            label="名称（英文标识）"
            placeholder="data-science"
            hint="小写字母、数字和 .-_，作为角色的唯一 ID"
            persistent-hint
            density="compact"
            variant="outlined"
            class="mb-3"
          />
          <v-text-field
            v-model="newRoleTitle"
            label="标题"
            placeholder="数据科学"
            density="compact"
            variant="outlined"
            class="mb-3"
          />
          <v-text-field
            v-model="newRoleDescription"
            label="描述（一句话，显示在下拉里）"
            placeholder="统计分析、机器学习与数据可视化"
            density="compact"
            variant="outlined"
            class="mb-3"
          />
          <v-textarea
            v-model="newRoleBody"
            label="Persona 正文（注入芝士的 system prompt）"
            placeholder="你是一位数据科学导师，擅长……"
            rows="6"
            density="compact"
            variant="outlined"
            auto-grow
          />
          <v-alert
            v-if="roleFormError"
            type="error"
            density="compact"
            class="mb-2"
          >
            {{ roleFormError }}
          </v-alert>
        </v-card-text>
        <v-card-actions class="px-6 pb-4">
          <v-spacer />
          <v-btn variant="text" @click="roleDialog = false">取消</v-btn>
          <v-btn
            color="primary"
            variant="flat"
            :loading="creatingRole"
            :disabled="!newRoleName.trim() || !newRoleBody.trim()"
            @click="submitNewRole"
          >
            创建并使用
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped>
.settings-page {
  background: var(--canvas);
}
/* Section rhythm (the ln-* classes are scoped to OverviewView, so style them
   here). Flat sections: a title, then the pool rows are the cards. */
.ln-section {
  margin-bottom: 26px;
}
.ln-section-head {
  display: flex;
  align-items: center;
  margin-bottom: 12px;
}
.ln-section-title {
  font-size: 15px;
  font-weight: 600;
}
.ln-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.pool-row {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 12px;
  text-align: left;
  padding: 12px 14px;
  border: 1px solid rgba(var(--v-border-color), 0.55);
  border-radius: 10px;
  background: var(--surface);
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s, box-shadow 0.15s;
}
.pool-row:hover:not(:disabled) {
  border-color: rgba(var(--v-theme-primary), 0.5);
}
.pool-row--active {
  border-color: rgb(var(--v-theme-primary));
  background: rgba(var(--v-theme-primary), 0.05);
  box-shadow: 0 0 0 1px rgb(var(--v-theme-primary));
}
.pool-row:disabled {
  cursor: default;
  opacity: 0.7;
}
.pool-radio {
  flex: 0 0 auto;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  border: 2px solid rgba(var(--v-border-color), 0.9);
}
.pool-radio--on {
  border-color: rgb(var(--v-theme-primary));
  background:
    radial-gradient(
      circle,
      rgb(var(--v-theme-primary)) 0 4px,
      transparent 5px
    );
}
.pool-main {
  flex: 1;
  min-width: 0;
}
.pool-title {
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 8px;
}
.pool-tier {
  font-size: 0.68rem;
  font-weight: 500;
  padding: 1px 7px;
  border-radius: 10px;
  color: rgb(var(--v-theme-primary));
  background: rgba(var(--v-theme-primary), 0.1);
}
.pool-sub {
  font-size: 0.8rem;
  margin-top: 2px;
}
.pool-current {
  font-size: 0.74rem;
  color: rgb(var(--v-theme-primary));
  font-weight: 600;
}
.link {
  color: rgb(var(--v-theme-primary));
  cursor: pointer;
  text-decoration: underline;
  text-underline-offset: 2px;
}
</style>
