<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  getComputeProfiles,
  getExecutionProfiles,
  getProject,
  setComputeProfile,
  setExecutionProfile,
} from '../api'
import type { ComputeProfiles, ExecProfiles } from '../types'

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
const savingAi = ref<string | null>(null)
const savingCompute = ref<string | null>(null)

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
    const [proj, execP, compP] = await Promise.all([
      getProject(props.projectId),
      getExecutionProfiles(props.projectId),
      getComputeProfiles(props.projectId),
    ])
    projectName.value = proj.name
    ai.value = execP
    compute.value = compP
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
      </template>
    </v-container>
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
