<script setup lang="ts">
/**
 * 后方「棘轮」这一屏的画面：它只收结果，不取数、不读地址、不碰 store。
 *
 * 路由页 `AdminRatchetPage.vue` 拿数据（`ratchetApi`），这一层负责画。分开的理由
 * 不只是整洁：这一屏是「场景」，给一组 props 就得能单独挂起来（`pnpm run lint:scenes`
 * 的 A 档），所以它不能有自己取数的能力。
 *
 * 四条画法，都是这一页存在的理由：
 *
 * 1. **0 和「没量到」是两件事。** 某道检查这次没跑到、这次采集整个失败，写的都是
 *    「没跑到」，不写 0。0 的意思是「量过了、没有问题」。
 * 2. **冻结数和违规数分开。** 「已登记多少条豁免」是一次决定，「实际还欠多少」是一次
 *    测量。合成一个数，人就没法判断变化是债还了还是基准放宽了。
 * 3. **规则变过的两段不比。** 判定文件内容变了（快照里每道检查带一个规则指纹），
 *    前后就不是同一种量法；页面标出那一点，从它后面重新开始算，不把它算成还债或退步。
 * 4. **没积够就不画走势。** 归档里只有一个点时，这一页说「还比不出来」，不画一条平线
 *    冒充「一直没动」。
 *
 * 还有两组状态也是两件事，不能合成一支：
 *
 * - **读不出来**（props `failed`，且手上没有 board）和**还没有采集**（`points === 0`）：
 *   前者是这次请求失败了，后者是归档本来就空。所以读失败那一支多带一个 `!board`——
 *   一次成功的刷新足以把这一屏从读失败里救出来，不该被上一屏的错继续盖着。
 * - **归档里有采集、却没有一道检查**（`points > 0` 而 `areas` 为空）和**一条都没有归档**：
 *   真实契约就是这样（一次失败的采集会入档成一条洞，`points=1`、`total_stored=1`、
 *   `areas=[]`）。把它当成「一条都没归档」会把失败历史和采集来源一起藏起来，正好藏掉
 *   唯一能解释「为什么是空的」的那句话。
 *
 * 页头那行「采集提交」和「部署提交」是两个不同的东西：数据量的是哪个提交，和线上
 * 现在跑的是哪个版本，混起来就会出现「这份数据说的是线上」这种没根据的话。
 */
import type { RatchetBoard } from '@/views/admin/ratchetApi'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminEmptyState from '@/components/admin/AdminEmptyState.vue'
import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'
import AdminRatchetArea from '@/components/admin/ratchet/AdminRatchetArea.vue'

defineOptions({ name: 'AdminRatchetPageView' })

const props = withDefaults(
  defineProps<{
    board?: RatchetBoard | null
    loading?: boolean
    /** 上一次读取这个归档失败了。有 board 的时候不显示这一支（见上）。 */
    failed?: boolean
    /** 最近一次拉取的结果（成功或失败都留着），空串表示还没拉过。 */
    pull?: string
    pullFailed?: boolean
    refreshing?: boolean
  }>(),
  { board: null, loading: false, failed: false, pull: '', pullFailed: false, refreshing: false }
)

const emit = defineEmits<{ refresh: []; retry: [] }>()

const { t } = useI18n()

const areas = computed(() => props.board?.areas ?? [])
const checks = computed(() => areas.value.reduce((n, area) => n + area.checks.length, 0))
/** 这次 board 里带回来的点数。**不是归档总数**：后端只取最近 `ratchet_series_points`
 *  条（现在 60），更早的留在库里（`total_stored`）。两个数在页面上各说各的话，不能
 *  混成一个——历史超过窗口时，「已归档 N 次」说小的那个就成了假的。 */
const collections = computed(() => props.board?.points ?? 0)
/** 归档里一共有几次采集。 */
const stored = computed(() => props.board?.total_stored ?? 0)
const short = (sha: string | null, width = 10) => (sha ? sha.slice(0, width) : '—')
const stamp = (iso: string | null) => (iso ? `${iso.slice(0, 16).replace('T', ' ')}Z` : '—')

/** 归档里最后一次采集自己的说法。`collections` 从旧到新排，最后一个是最近一次。 */
const latestReason = computed(() => {
  const list = props.board?.collections ?? []
  return list.length ? list[list.length - 1]?.reason ?? '' : ''
})

/** 「有采集、但一条测量都没有」那一段的说辞：采集自己报了失败就把原因带上，没有原因
 *  就只说没有测量——不能替它编一个理由。
 *
 *  句子里的数只能是**这次 board 带回来的这几次**（`collections`），不能读成整个归档：
 *  归档超过窗口时（比如 61 次、只带回最新 60 次），「归档里 60 次都没有测量」是对全
 *  历史的误述——那 1 次更早的成功采集不在这一屏里，也没被这句话数进去。归档总数在
 *  上面来源行按 `total_stored` 单说。 */
const noMeasurementsDesc = computed(() => {
  const reason = latestReason.value
  const reportedFailure = props.board?.collection && props.board.collection !== 'ok'
  return reportedFailure && reason
    ? t('ratchet.state.noMeasurementsFailed', { count: collections.value, reason })
    : t('ratchet.state.noMeasurementsPlain', { count: collections.value })
})

/** 「最近一次采集失败」那一行，下面还有表可看时用它。说的是**这一次**：表里每一格
 *  画的就是这一次的回答，「还没测到」是没量到，不是 0。这里原先写的是「上面这些数
 *  来自更早的一次」——那是句假话：「现在」那一格画的是最新那个点，而采集失败的那次
 *  在归档里没有检查记录，所以每一格都落成「还没测到」，上面根本没有数。原因照抄
 *  采集自己写下的那句话，没写就不替它编。 */
const collectionFailedLine = computed(() => {
  const reason = latestReason.value
  return reason ? t('ratchet.prov.collectionFailed', { reason }) : t('ratchet.prov.collectionFailedNoReason')
})
</script>

<template>
  <div class="arc">
    <AdminPageHeader :title="t('ratchet.page.title')" :sub="t('ratchet.page.subtitle')">
      <template #tools>
        <button type="button" class="arc__btn" :disabled="refreshing" @click="emit('refresh')">
          {{ refreshing ? t('ratchet.action.refreshing') : t('ratchet.action.refresh') }}
        </button>
      </template>
    </AdminPageHeader>

    <div class="arc__inner page-container--admin">
      <p v-if="pull" class="arc__pull" :class="{ 'arc__pull--bad': pullFailed }">{{ pull }}</p>

      <div v-if="loading" class="arc__skel">
        <span v-for="n in 3" :key="n" class="arc__bone" />
      </div>

      <!-- 读失败**不是**「还没有采集」：两句话，两个画面。board 拿到过就不再走这一支。 -->
      <AdminEmptyState
        v-else-if="failed && !board"
        :title="t('ratchet.state.loadFailed')"
        :desc="t('ratchet.state.loadFailedDesc')"
        :action="t('ratchet.state.retry')"
        tone="error"
        @action="emit('retry')"
      />

      <AdminEmptyState
        v-else-if="!board || !collections"
        :title="t('ratchet.state.empty')"
        :desc="t('ratchet.state.emptyDesc')"
        :action="t('ratchet.state.emptyAction')"
        @action="emit('refresh')"
      />

      <template v-else>
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
            {{ t('ratchet.prov.archived', { count: stored }) }}
          </span>
          <span>
            {{ t('ratchet.prov.deployed') }}
            <b class="arc__mono">{{ short(board.deployed_commit) }}</b>
          </span>
          <a v-if="board.run_url" class="arc__link" :href="board.run_url" target="_blank" rel="noopener">
            {{ t('ratchet.prov.run') }}
          </a>
        </p>

        <!-- 归档里一条测量都没有时不说这一句：那时整页走「这次没有可用的测量」，两句话
             说的是同一件事，说两遍就成了两种说法。 -->
        <p v-if="checks && board.collection && board.collection !== 'ok'" class="arc__pull arc__pull--bad">
          {{ collectionFailedLine }}
        </p>

        <!-- 只有一个点的时候不画走势：这一句解释为什么下面是空的，也解释了什么时候会有。 -->
        <p v-if="collections < 2" class="arc__note">{{ t('ratchet.note.singlePoint') }}</p>

        <AdminEmptyState
          v-if="!checks"
          :title="t('ratchet.state.noMeasurements')"
          :desc="noMeasurementsDesc"
          :action="t('ratchet.state.emptyAction')"
          @action="emit('refresh')"
        />

        <template v-else>
          <ul class="arc__legend t-meta">
            <li>{{ t('ratchet.legend.actual') }}</li>
            <li>{{ t('ratchet.legend.notCollected') }}</li>
            <li>{{ t('ratchet.legend.frozen') }}</li>
            <li>{{ t('ratchet.legend.ruleChanged') }}</li>
            <li>{{ t('ratchet.legend.newExemptions') }}</li>
            <li>{{ t('ratchet.legend.stale') }}</li>
            <li>{{ t('ratchet.legend.failed') }}</li>
          </ul>

          <AdminRatchetArea v-for="area in areas" :key="area.area" :area="area.area" :checks="area.checks" />

          <p class="arc__foot t-meta">
            {{ t('ratchet.foot.source', { repo: board.repo, checks }) }}
          </p>
        </template>
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
