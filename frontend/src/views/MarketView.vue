<script setup lang="ts">
import type { MarketPools, PoolListing } from '../cx_types'

import { computed, onMounted, ref } from 'vue'

import { getMarketPools } from '../api'
import NodeBoard from '../components/NodeBoard.vue'
import TaskMarket from '../components/TaskMarket.vue'

// 市场 has two faces (spec §13 阶段 6 + design v3):
//   题目匹配 — Spaces publish 题目 (Task Templates), teams apply with a project.
//   算力资源 — the resource-pool catalog (AI models + compute) a project can
//   select from in its 设置. The compute tab also hosts the 节点状态 board.
const tab = ref<'tasks' | 'pools'>('tasks')

const pools = ref<MarketPools | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)

const TIER_LABEL: Record<string, string> = {
  default: '默认 · 包含',
  included: '包含',
  testing: '内测',
  byo: '自带凭证',
  premium: '增值',
}

const aiPools = computed<PoolListing[]>(() => pools.value?.ai ?? [])
const computePools = computed<PoolListing[]>(() => pools.value?.compute ?? [])

async function load() {
  loading.value = true
  error.value = null
  try {
    pools.value = await getMarketPools()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载市场失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="market-page fill-height overflow-y-auto">
    <v-container class="py-6" style="max-width: 1080px">
      <div class="mb-4">
        <div class="t-eyebrow mb-1">市场</div>
        <h1 class="t-page-title">匹配与资源</h1>
      </div>

      <v-tabs v-model="tab" density="comfortable" color="primary" class="mb-5">
        <v-tab value="tasks"> <v-icon size="18" class="me-2">mdi-handshake-outline</v-icon>题目匹配 </v-tab>
        <v-tab value="pools"> <v-icon size="18" class="me-2">mdi-server</v-icon>算力资源 </v-tab>
      </v-tabs>

      <v-window v-model="tab">
        <!-- 题目匹配: Space 发布的题目 + 团队应征 (spec §13 阶段 6) -->
        <v-window-item value="tasks">
          <p class="t-body c-muted mb-5" style="max-width: 660px">
            机构把 <strong>题目</strong> 发布到市场；团队用自己的项目
            <strong>应征</strong>。应征被接受后，项目自动链接到题目，资源包与条件即刻生效。
          </p>
          <TaskMarket />
        </v-window-item>

        <!-- 算力资源: the original resource-pool catalog, moved verbatim. -->
        <v-window-item value="pools">
          <p class="t-body c-muted mb-5" style="max-width: 660px">
            知是把 <strong>AI 模型</strong> 和 <strong>算力</strong> 都看作资源池。默认的资源池由平台补贴，
            开箱即用；更强的模型、你自己的机器、带 GPU 的算力也在这里上架。在任意项目的
            <strong>设置 → 资源池</strong> 里挑选要用的池。
          </p>

          <!-- 节点状态: live board of compute nodes (local + cheesed remote),
               self-contained in components/NodeBoard.vue. -->
          <NodeBoard />

          <div v-if="loading" class="d-flex justify-center py-10">
            <v-progress-circular indeterminate color="primary" />
          </div>
          <v-alert v-else-if="error" type="error" density="comfortable">
            {{ error }}
          </v-alert>

          <template v-else>
            <div class="market-group">
              <div class="market-group__head">
                <v-icon size="20" class="me-2 c-muted">mdi-brain</v-icon>
                <h2 class="market-group__title">AI 模型</h2>
                <span class="market-group__hint c-faint">每次对话使用的模型</span>
              </div>
              <div class="market-grid">
                <article
                  v-for="p in aiPools"
                  :key="p.id"
                  class="pool-card"
                  :class="{ 'pool-card--soon': !p.available }"
                >
                  <div class="pool-card__top">
                    <span class="pool-card__tier">{{ TIER_LABEL[p.tier] ?? p.tier }}</span>
                    <span v-if="p.default" class="pool-card__default">默认</span>
                  </div>
                  <h3 class="pool-card__title">{{ p.label }}</h3>
                  <div class="pool-card__price">{{ p.price }}</div>
                  <p class="pool-card__desc c-muted">{{ p.description }}</p>
                  <div class="pool-card__foot">
                    <span v-if="p.available" class="pool-card__ok">
                      <v-icon size="14">mdi-check-circle</v-icon> 可在项目里选用
                    </span>
                    <span v-else class="pool-card__soon-tag">暂未开通</span>
                  </div>
                </article>
              </div>
            </div>

            <div class="market-group">
              <div class="market-group__head">
                <v-icon size="20" class="me-2 c-muted">mdi-server</v-icon>
                <h2 class="market-group__title">算力</h2>
                <span class="market-group__hint c-faint">运行任务的机器</span>
              </div>
              <div class="market-grid">
                <article
                  v-for="p in computePools"
                  :key="p.id"
                  class="pool-card"
                  :class="{ 'pool-card--soon': !p.available }"
                >
                  <div class="pool-card__top">
                    <span class="pool-card__tier">{{ TIER_LABEL[p.tier] ?? p.tier }}</span>
                    <span v-if="p.default" class="pool-card__default">默认</span>
                  </div>
                  <h3 class="pool-card__title">{{ p.label }}</h3>
                  <div class="pool-card__price">{{ p.price }}</div>
                  <p class="pool-card__desc c-muted">{{ p.description }}</p>
                  <div class="pool-card__foot">
                    <span v-if="p.available" class="pool-card__ok">
                      <v-icon size="14">mdi-check-circle</v-icon> 可在项目里选用
                    </span>
                    <span v-else class="pool-card__soon-tag">暂未开通</span>
                  </div>
                </article>
              </div>
            </div>
          </template>
        </v-window-item>
      </v-window>
    </v-container>
  </div>
</template>

<style scoped>
.market-page {
  background: var(--canvas);
}
.market-group {
  margin-bottom: 34px;
}
.market-group__head {
  display: flex;
  align-items: baseline;
  margin-bottom: 14px;
}
.market-group__title {
  font-size: 1.05rem;
  font-weight: 600;
}
.market-group__hint {
  margin-left: 10px;
  font-size: 0.8rem;
}
.market-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 14px;
}
.pool-card {
  border: 1px solid rgba(var(--v-border-color), 0.55);
  border-radius: 12px;
  padding: 16px;
  background: var(--surface);
  display: flex;
  flex-direction: column;
  transition:
    box-shadow 0.15s,
    border-color 0.15s;
}
.pool-card:hover {
  border-color: rgba(var(--v-theme-primary), 0.5);
  box-shadow: var(--shadow-1);
}
.pool-card--soon {
  opacity: 0.72;
}
.pool-card__top {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
}
.pool-card__tier {
  font-size: 0.68rem;
  font-weight: 500;
  padding: 1px 8px;
  /* 小标签 → --radius-sm，和 .chip-neutral / .ln-tag 同档（原来是 10px，不在阶梯上）。 */
  border-radius: var(--radius-sm);
  color: rgb(var(--v-theme-primary));
  background: rgba(var(--v-theme-primary), 0.1);
}
.pool-card__default {
  font-size: 0.66rem;
  color: var(--muted);
  border: 1px solid rgba(var(--v-border-color), 0.7);
  padding: 0 6px;
  border-radius: var(--radius-sm);
}
.pool-card__title {
  font-size: 1rem;
  font-weight: 600;
  line-height: 1.3;
}
.pool-card__price {
  font-size: 0.82rem;
  color: rgb(var(--v-theme-primary));
  font-weight: 600;
  margin: 2px 0 8px;
}
.pool-card__desc {
  font-size: 0.82rem;
  line-height: 1.55;
  flex: 1;
}
.pool-card__foot {
  margin-top: 12px;
  font-size: 0.76rem;
}
.pool-card__ok {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  /* “可用”是真实状态 → 状态色的文字档（§1.5）。原来那个 #35b37e 是外来绿。 */
  color: var(--ok-ink);
}
.pool-card__soon-tag {
  color: var(--faint);
}
</style>
