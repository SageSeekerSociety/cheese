<script setup lang="ts">
import type { MarketPools, PoolListing } from '../cx_types'

import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { getMarketPools } from '../api'
import NodeBoard from '../components/NodeBoard.vue'

// 市场 has two faces (spec §13 阶段 6 + design v3):
//   题目匹配 — Spaces publish 题目 (Task Templates), teams apply with a project.
//   模型与工作电脑 — the catalog (AI models + work computers) a project can
//   select from in its 设置. The compute tab also hosts the 节点状态 board.
const { t } = useI18n()

const tab = ref<'tasks' | 'pools'>('tasks')

const pools = ref<MarketPools | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)

const TIER_LABEL: Record<string, string> = {
  default: 'market.tier.default',
  included: 'market.tier.included',
  testing: 'market.tier.testing',
  byo: 'market.tier.byo',
  premium: 'market.tier.premium',
}
const tierLabel = (tier: string) => (TIER_LABEL[tier] ? t(TIER_LABEL[tier]) : tier)

const aiPools = computed<PoolListing[]>(() => pools.value?.ai ?? [])
const computePools = computed<PoolListing[]>(() => pools.value?.compute ?? [])

async function load() {
  loading.value = true
  error.value = null
  try {
    pools.value = await getMarketPools()
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('market.loadFailed')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="fill-height overflow-y-auto">
    <v-container class="py-6" style="max-width: 1080px">
      <!-- 手机上页名写在顶栏里，这里不再写一遍。 -->
      <div v-if="$vuetify.display.mdAndUp" class="mb-4">
        <div class="t-eyebrow mb-1">{{ t('market.eyebrow') }}</div>
        <h1 class="t-page-title">{{ t('market.title') }}</h1>
      </div>

      <v-tabs v-model="tab" density="comfortable" color="primary" class="mb-5">
        <v-tab value="tasks">
          <v-icon size="18" class="me-2">mdi-handshake-outline</v-icon>{{ t('market.tabs.tasks') }}
        </v-tab>
        <v-tab value="pools"> <v-icon size="18" class="me-2">mdi-server</v-icon>{{ t('market.tabs.pools') }} </v-tab>
      </v-tabs>

      <v-window v-model="tab">
        <!-- 题目匹配: Space 发布的题目 + 团队应征 (spec §13 阶段 6) -->
        <v-window-item value="tasks">
          <i18n-t
            scope="global"
            keypath="market.tasksIntro"
            tag="p"
            class="t-body c-muted mb-5"
            style="max-width: 660px"
          >
            <template #challenges>
              <strong>{{ t('market.tasksIntroChallenges') }}</strong>
            </template>
            <template #apply>
              <strong>{{ t('market.tasksIntroApply') }}</strong>
            </template>
          </i18n-t>
        </v-window-item>

        <!-- 模型与工作电脑: the catalog of AI models and work computers. -->
        <v-window-item value="pools">
          <i18n-t
            scope="global"
            keypath="market.poolsIntro"
            tag="p"
            class="t-body c-muted mb-5"
            style="max-width: 660px"
          >
            <template #models>
              <strong>{{ t('market.aiModels') }}</strong>
            </template>
            <template #computers>
              <strong>{{ t('market.workComputers') }}</strong>
            </template>
          </i18n-t>

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
                <h2 class="market-group__title">{{ t('market.aiModels') }}</h2>
                <span class="market-group__hint c-faint">{{ t('market.aiModelsHint') }}</span>
              </div>
              <div class="market-grid">
                <article
                  v-for="p in aiPools"
                  :key="p.id"
                  class="pool-card"
                  :class="{ 'pool-card--soon': !p.available }"
                >
                  <div class="pool-card__top">
                    <span class="pool-card__tier">{{ tierLabel(p.tier) }}</span>
                    <span v-if="p.default" class="pool-card__default">{{ t('market.default') }}</span>
                  </div>
                  <h3 class="pool-card__title">{{ p.label }}</h3>
                  <div class="pool-card__price">{{ p.price }}</div>
                  <p class="pool-card__desc c-muted">{{ p.description }}</p>
                  <div class="pool-card__foot">
                    <span v-if="p.available" class="pool-card__ok">
                      <v-icon size="14">mdi-check-circle</v-icon> {{ t('market.available') }}
                    </span>
                    <span v-else class="pool-card__soon-tag">{{ t('market.unavailable') }}</span>
                  </div>
                </article>
              </div>
            </div>

            <div class="market-group">
              <div class="market-group__head">
                <v-icon size="20" class="me-2 c-muted">mdi-server</v-icon>
                <h2 class="market-group__title">{{ t('market.workComputers') }}</h2>
                <span class="market-group__hint c-faint">{{ t('market.workComputersHint') }}</span>
              </div>
              <div class="market-grid">
                <article
                  v-for="p in computePools"
                  :key="p.id"
                  class="pool-card"
                  :class="{ 'pool-card--soon': !p.available }"
                >
                  <div class="pool-card__top">
                    <span class="pool-card__tier">{{ tierLabel(p.tier) }}</span>
                    <span v-if="p.default" class="pool-card__default">{{ t('market.default') }}</span>
                  </div>
                  <h3 class="pool-card__title">{{ p.label }}</h3>
                  <div class="pool-card__price">{{ p.price }}</div>
                  <p class="pool-card__desc c-muted">{{ p.description }}</p>
                  <div class="pool-card__foot">
                    <span v-if="p.available" class="pool-card__ok">
                      <v-icon size="14">mdi-check-circle</v-icon> {{ t('market.available') }}
                    </span>
                    <span v-else class="pool-card__soon-tag">{{ t('market.unavailable') }}</span>
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
  /* See NodeBoard: without min(), 260px is a floor the grid keeps even in a
     narrower column, and the board overflows rather than reflowing. */
  grid-template-columns: repeat(auto-fill, minmax(min(260px, 100%), 1fr));
  gap: 14px;
}
.pool-card {
  border: 1px solid rgba(var(--v-border-color), 0.55);
  border-radius: 12px;
  padding: 16px;
  background: var(--surface);
  display: flex;
  flex-direction: column;
  transition: border-color var(--dur-quick) var(--ease-standard);
}
.pool-card:hover {
  border-color: rgba(var(--v-theme-primary), 0.5);
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
