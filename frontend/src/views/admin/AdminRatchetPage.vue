<script setup lang="ts">
import type { RatchetBoard } from '@/views/admin/ratchetApi'

import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminEmptyState from '@/components/admin/AdminEmptyState.vue'
import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'
import AdminRatchetArea from '@/components/admin/ratchet/AdminRatchetArea.vue'
import { getRatchetBoard, refreshRatchetBoard } from '@/views/admin/ratchetApi'

// 后台的「棘轮」（`/admin/ratchet`）：架构还债进度，按方面分区。
//
// 这一页是**只读的**：它不定义任何一条判定，只把采集脚本每道检查自己吐出来的数
// 画出来。规则在各自的检查器里（`.claude/scripts/ratchet-snapshot.py` 是那份清单），
// 采集在 `.github/workflows/arch-metrics.yml` 里跑，后端把每次采集的工件存下来
// （`POST /admin/ratchet/refresh` 是唯一会去 GitHub 的动作），这里读存档。所以这一页
// 上不可能出现一个「检查器没量过」的数。
//
// 四条画法，都是这一页存在的理由：
//
// 1. **0 和「没量到」是两件事。** 某道检查这次没跑到、这次采集整个失败，写的都是
//    「没跑到」，不写 0。0 的意思是「量过了、没有问题」。
// 2. **冻结数和违规数分开。** 「已登记多少条豁免」是一次决定，「实际还欠多少」是一次
//    测量。合成一个数，人就没法判断变化是债还了还是基准放宽了。
// 3. **规则变过的两段不比。** 判定文件内容变了（快照里每道检查带一个规则指纹），
//    前后就不是同一种量法；页面标出那一点，从它后面重新开始算，不把它算成还债或退步。
// 4. **没积够就不画走势。** 归档里只有一个点时，这一页说「还比不出来」，不画一条平线
//    冒充「一直没动」。
//
// 页头那行「采集提交」和「部署提交」是两个不同的东西：数据量的是哪个提交，和线上
// 现在跑的是哪个版本，混起来就会出现「这份数据说的是线上」这种没根据的话。
defineOptions({ name: 'AdminRatchetPage' })

const { t } = useI18n()

const board = ref<RatchetBoard | null>(null)
const loading = ref(true)
const failed = ref(false)
/** 拉取的结果（成功或失败）留在页面上：一次拉取改的是存档，不是这一页的样子，
 *  说清楚「这次拉到了什么」比把提示一闪而过重要。 */
const pull = ref('')
const pullFailed = ref(false)
const refreshing = ref(false)

const areas = computed(() => board.value?.areas ?? [])
const checks = computed(() => areas.value.reduce((n, area) => n + area.checks.length, 0))
const collections = computed(() => board.value?.points ?? 0)
const short = (sha: string | null, width = 10) => (sha ? sha.slice(0, width) : '—')
const stamp = (iso: string | null) => (iso ? `${iso.slice(0, 16).replace('T', ' ')}Z` : '—')

async function load() {
  loading.value = true
  failed.value = false
  try {
    board.value = await getRatchetBoard()
  } catch {
    // 读失败**不是**「还没有采集」：两句话，两个画面。
    failed.value = true
  } finally {
    loading.value = false
  }
}

async function refresh() {
  refreshing.value = true
  pullFailed.value = false
  pull.value = ''
  try {
    const next = await refreshRatchetBoard()
    board.value = next
    const report = next.refresh
    if (!report) return
    if (report.error) {
      pullFailed.value = true
      pull.value = t('ratchet.pull.failed', { error: report.error })
      return
    }
    pull.value = t('ratchet.pull.done', {
      listed: report.listed,
      stored: report.stored,
      already: report.already_stored,
    })
  } catch {
    pullFailed.value = true
    pull.value = t('ratchet.pull.unreachable')
  } finally {
    refreshing.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="arc">
    <AdminPageHeader :title="t('ratchet.page.title')" :sub="t('ratchet.page.subtitle')">
      <template #tools>
        <button type="button" class="arc__btn" :disabled="refreshing" @click="refresh">
          {{ refreshing ? t('ratchet.action.refreshing') : t('ratchet.action.refresh') }}
        </button>
      </template>
    </AdminPageHeader>

    <div class="arc__inner page-container--admin">
      <p v-if="pull" class="arc__pull" :class="{ 'arc__pull--bad': pullFailed }">{{ pull }}</p>

      <div v-if="loading" class="arc__skel">
        <span v-for="n in 3" :key="n" class="arc__bone" />
      </div>

      <AdminEmptyState
        v-else-if="failed"
        :title="t('ratchet.state.loadFailed')"
        :desc="t('ratchet.state.loadFailedDesc')"
        :action="t('ratchet.state.retry')"
        tone="error"
        @action="load"
      />

      <AdminEmptyState
        v-else-if="!checks"
        :title="t('ratchet.state.empty')"
        :desc="t('ratchet.state.emptyDesc')"
        :action="t('ratchet.state.emptyAction')"
        @action="refresh"
      />

      <template v-else-if="board">
        <!-- 数据从哪来。这一块是这一页可信度的全部依据：没有它，下面每个数都只是
             「某个时候的某个东西」。 -->
        <p class="arc__prov t-meta">
          <span>
            {{ t('ratchet.prov.collected') }}
            <b class="arc__mono">{{ short(board.collected_commit) }}</b>
          </span>
          <span
            >{{ t('ratchet.prov.commitTime') }} <b>{{ stamp(board.collected_at) }}</b></span
          >
          <span>
            {{ t('ratchet.prov.archived', { count: collections }) }}
          </span>
          <span>
            {{ t('ratchet.prov.deployed') }}
            <b class="arc__mono">{{ short(board.deployed_commit) }}</b>
          </span>
          <a v-if="board.run_url" class="arc__link" :href="board.run_url" target="_blank" rel="noopener">
            {{ t('ratchet.prov.run') }}
          </a>
        </p>

        <p v-if="board.collection && board.collection !== 'ok'" class="arc__pull arc__pull--bad">
          {{ t('ratchet.prov.collectionFailed') }}
        </p>

        <!-- 只有一个点的时候不画走势：这一句解释为什么下面是空的，也解释了什么时候会有。 -->
        <p v-if="collections < 2" class="arc__note">{{ t('ratchet.note.singlePoint') }}</p>

        <ul class="arc__legend t-meta">
          <li>{{ t('ratchet.legend.actual') }}</li>
          <li>{{ t('ratchet.legend.notCollected') }}</li>
          <li>{{ t('ratchet.legend.frozen') }}</li>
          <li>{{ t('ratchet.legend.ruleChanged') }}</li>
          <li>{{ t('ratchet.legend.newExemptions') }}</li>
          <li>{{ t('ratchet.legend.stale') }}</li>
          <li>{{ t('ratchet.legend.failed') }}</li>
        </ul>

        <AdminRatchetArea
          v-for="area in areas"
          :key="area.area"
          :area="area.area"
          :checks="area.checks"
          :collections="collections"
        />

        <p class="arc__foot t-meta">
          {{ t('ratchet.foot.source', { repo: board.repo, checks }) }}
        </p>
      </template>
    </div>
  </div>
</template>

<style scoped>
.arc {
  display: flex;
  flex-direction: column;
  min-height: 100%;
}

.arc__inner {
  padding: 16px 24px 32px;
}

.arc__mono {
  font-family: var(--font-mono);
}

.arc__btn {
  padding: 5px 12px;
  border: 1px solid var(--line-2);
  border-radius: 8px;
  background: var(--surface);
  color: var(--text);
  font-size: 13px;
  cursor: pointer;
}

.arc__btn:disabled {
  color: var(--faint);
  cursor: default;
}

.arc__prov {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 18px;
  margin: 12px 0 0;
  color: var(--muted);
}

.arc__prov b {
  color: var(--ink);
  font-weight: 600;
}

.arc__link {
  color: var(--accent-ink);
  text-decoration: none;
}

.arc__link:hover {
  text-decoration: underline;
}

.arc__pull {
  margin: 12px 0 0;
  padding: 8px 12px;
  border: 1px solid var(--line-2);
  border-radius: 8px;
  background: var(--fill);
  font-size: 12.5px;
}

.arc__pull--bad {
  border-color: var(--danger-ink);
  background: var(--danger-wash);
  color: var(--danger-ink);
}

.arc__note {
  margin: 12px 0 0;
  padding: 8px 12px;
  border: 1px dashed var(--line-2);
  border-radius: 8px;
  color: var(--muted);
  font-size: 12.5px;
}

.arc__legend {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 16px;
  margin: 10px 0 0;
  padding: 0;
  list-style: none;
  color: var(--muted);
}

.arc__foot {
  margin: 14px 0 0;
  color: var(--faint);
}

.arc__skel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 16px;
}

.arc__bone {
  height: 96px;
  border-radius: var(--radius-lg);
  background: var(--fill);
}
</style>
