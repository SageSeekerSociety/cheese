<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { getMarketPools } from '../api'
import type { MarketPools, PoolListing } from '../types'

// 市场 (design v3): browse every resource pool on offer — AI models and compute —
// as a single catalog. AI and compute are symmetric pools; some are included in
// the platform, others are bring-your-own or paid. A project chooses which pools
// it runs on in its own 设置.
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
      <div class="mb-6">
        <div class="t-eyebrow mb-1">市场</div>
        <h1 class="t-page-title">资源池</h1>
        <p class="t-body c-muted mt-1" style="max-width: 660px">
          知是把 <strong>AI 模型</strong> 和 <strong>算力</strong> 都看成资源池。默认的池平台已经补贴，
          开箱即用；更强的模型、你自己的机器、或带 GPU 的算力也在这里上架。
          在任意项目的 <strong>设置 → 资源池</strong> 里挑选要用的池。
        </p>
      </div>

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
            <span class="market-group__hint c-faint">一次对话跑在哪个模型上</span>
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
                <span v-else class="pool-card__soon-tag">敬请期待 / 按需开通</span>
              </div>
            </article>
          </div>
        </div>

        <div class="market-group">
          <div class="market-group__head">
            <v-icon size="20" class="me-2 c-muted">mdi-server</v-icon>
            <h2 class="market-group__title">算力</h2>
            <span class="market-group__hint c-faint">干活（跑沙箱）用哪台机器</span>
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
                <span v-else class="pool-card__soon-tag">敬请期待 / 按需开通</span>
              </div>
            </article>
          </div>
        </div>
      </template>
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
  transition: box-shadow 0.15s, border-color 0.15s;
}
.pool-card:hover {
  border-color: rgba(var(--v-theme-primary), 0.5);
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.06);
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
  border-radius: 10px;
  color: rgb(var(--v-theme-primary));
  background: rgba(var(--v-theme-primary), 0.1);
}
.pool-card__default {
  font-size: 0.66rem;
  color: var(--muted);
  border: 1px solid rgba(var(--v-border-color), 0.7);
  padding: 0 6px;
  border-radius: 10px;
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
  color: #35b37e;
}
.pool-card__soon-tag {
  color: var(--faint);
}
</style>
