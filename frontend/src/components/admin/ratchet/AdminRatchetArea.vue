<script setup lang="ts">
import type { RatchetCheck, RatchetPoint, RatchetStaleExemption } from '@/views/admin/ratchetApi'

import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminRatchetSparkline from '@/components/admin/ratchet/AdminRatchetSparkline.vue'

// 一个「方面」（场景 / 边界 / 规模 / 类型与样式）里的一整块：表 + 每道检查可展开的详情。
//
// 分区和顺序都跟着服务端来（`areas` 的顺序就是采集器登记表的顺序），加一道检查不必
// 改这里。检查名的中文是**这一层的映射**，映射里没有的 id 就照原样显示英文 id ——
// 显示一个没翻译的名字，比把这道检查从表里藏起来好。
defineOptions({ name: 'AdminRatchetArea' })

const props = defineProps<{
  area: string
  checks: RatchetCheck[]
}>()

const { t } = useI18n()

const CHECK_KEYS: Record<string, string> = {
  'scene-ratchet': 'ratchet.check.sceneRatchet',
  'catalog-ratchet': 'ratchet.check.catalogRatchet',
  'fe-boundary': 'ratchet.check.feBoundary',
  'be-contracts': 'ratchet.check.beContracts',
  'be-deferred-imports': 'ratchet.check.beDeferredImports',
  'domain-import-guard': 'ratchet.check.domainImportGuard',
  'harness-boundary': 'ratchet.check.harnessBoundary',
  'is-private-read-points': 'ratchet.check.isPrivateReadPoints',
  'file-sizes': 'ratchet.check.fileSizes',
  'vue-tsc': 'ratchet.check.vueTsc',
  'stylelint-tokens': 'ratchet.check.stylelintTokens',
  palette: 'ratchet.check.palette',
}

/** 方向只有四种，写死成表而不是拼键名：i18n 目录的检查脚本看的是源码里的字面键，
 *  拼出来的键在它眼里等于没人用，会被当成一条没人引用的翻译。 */
const DIRECTION_KEYS: Record<string, string> = {
  improving: 'ratchet.direction.improving',
  worse: 'ratchet.direction.worse',
  flat: 'ratchet.direction.flat',
  unknown: 'ratchet.direction.unknown',
}

const AREA_KEYS: Record<string, string> = {
  场景: 'ratchet.area.scenes',
  边界: 'ratchet.area.boundaries',
  规模: 'ratchet.area.size',
  类型与样式: 'ratchet.area.types',
}

const checkName = (id: string) => (CHECK_KEYS[id] ? t(CHECK_KEYS[id]) : id)
const areaName = computed(() => (AREA_KEYS[props.area] ? t(AREA_KEYS[props.area]) : props.area))

/** 展开的那几道。默认全收起：这一页要先回答「哪个方面在变」，逐条明细是第二步。 */
const open = ref<Set<string>>(new Set())
const toggle = (id: string) => {
  const next = new Set(open.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  open.value = next
}

const passed = computed(() => props.checks.filter((check) => check.status === 'pass').length)

/** 树级数字：只有规模区那一道带（服务端 `board.py` 的 `_tree_size`）。`undefined`
 *  是「这一道没有树级数字这回事」，`null` 是「有这回事、这次没量到」—— 两者不能合，
 *  合成一个就等于替没量到的那次报了一个数。 */
const tree = computed(() => props.checks.find((check) => check.tree !== undefined)?.tree)

/** 口径提示。全表只有 `file-sizes` 需要：它是 **diff 口径**的闸门，`actual` 数的是
 *  「这次改过、并且超了上限的文件」。不说这句，收起状态下没人会怀疑那个 0。 */
const SCOPE_KEYS: Record<string, string> = {
  'file-sizes': 'ratchet.check.fileSizesScope',
}

const short = (sha: string) => sha.slice(0, 8)

/** 归档里的时间一律按 UTC 显示，和 CI、快照里的字面一致 —— 换成本地时区之后，
 *  「哪次采集在前」就得靠读者自己做算术了。 */
const stamp = (iso: string | null) => (iso ? `${iso.slice(0, 16).replace('T', ' ')}Z` : '')

const number = (value: number | null) => (value === null ? null : value)

/** 末尾那一段同口径的点，和「这一格为什么不比」的原因。
 *
 *  规则指纹一变就开新的一段；段里中间一次采不到（`actual === null`）则**断在那里**，
 *  不是跳过 —— 跳过会把「失败那次之前 10、之后 3」减成一次改善，而那个数是这一页谁
 *  也验不了的。判据和 `backend/app/domain/ratchet/board.py` 里 `_direction` 一致：服
 *  务端已经用它判了方向，这里只把同一对端点摆出来给人看，两边不能分叉。
 *
 *  `brokenByHole` 只用来解释原因：段里有第二个点、只是被一次没采到隔开时，这一格写
 *  「隔着一次没跑到」，而不是「只有一个点」—— 后者是句假话。 */
function segment(check: RatchetCheck): { measured: RatchetPoint[]; brokenByHole: boolean } {
  const sameRule: RatchetPoint[] = []
  for (const point of [...check.points].reverse()) {
    if (sameRule.length && point.rule_fingerprint !== sameRule[sameRule.length - 1].rule_fingerprint) break
    sameRule.push(point)
  }
  sameRule.reverse()
  const measured: RatchetPoint[] = []
  for (const point of [...sameRule].reverse()) {
    if (point.actual === null) break
    measured.push(point)
  }
  measured.reverse()
  const broken = measured.length < 2 && sameRule.filter((point) => point.actual !== null).length >= 2
  return { measured, brokenByHole: broken }
}

const compare = (check: RatchetCheck): { from: number; to: number; delta: number } | null => {
  const { measured } = segment(check)
  if (measured.length < 2) return null
  const from = measured[0].actual as number
  const to = measured[measured.length - 1].actual as number
  return { from, to, delta: to - from }
}

const staleText = (entry: RatchetStaleExemption) =>
  [entry.file, entry.why].filter((part): part is string => Boolean(part)).join(' · ')

/** 明细的形状每道检查都不一样（有的是 `{file, grade, reasons}`，有的是 `{file, count}`），
 *  所以照实把标量摊开：认识的那几个列出来，剩下的原样显示 —— 认不出来就不显示，
 *  等于把这些数丢掉，而它们正是展开这一行要看的。 */
function extra(entry: unknown): string {
  if (entry === null || typeof entry !== 'object') return String(entry)
  return Object.entries(entry as Record<string, unknown>)
    .filter(([key]) => key !== 'file')
    .map(([key, value]) => {
      if (Array.isArray(value)) return `${key}×${value.length}`
      if (value === null || value === undefined) return `${key}=—`
      if (typeof value === 'object') return `${key}=…`
      return `${key}=${String(value)}`
    })
    .join(' ')
}

/** 明细里那一列文件名。明细的键各道检查不一样，没有 `file` 的照实写一个破折号 ——
 *  和上面 `extra()` 同一个理由：认不出来就丢掉，等于把展开这一行要看的数丢掉。 */
function detailFile(entry: unknown): string {
  if (entry === null || typeof entry !== 'object') return String(entry)
  const file = (entry as { file?: unknown }).file
  return typeof file === 'string' ? file : '—'
}

const detailsOf = (check: RatchetCheck) => {
  const last = check.points[check.points.length - 1]
  return last?.details ?? []
}

const reasonOf = (check: RatchetCheck) => {
  const last = check.points[check.points.length - 1]
  return last?.reason ?? ''
}
</script>

<template>
  <section class="ara admin-card">
    <div class="ara__head">
      <h3 class="ara__title t-title">{{ areaName }}</h3>
      <span class="ara__count t-meta-read t-num">{{ t('ratchet.area.count', { count: checks.length }) }}</span>
      <span class="ara__count t-meta-read t-num">{{ t('ratchet.area.passed', { passed }) }}</span>
    </div>

    <!-- 树级数字。表里那一行是 diff 口径的 0（这次没有文件被判定过），这一行才是
         「树上到底超了多少」。量不到时写「未知」，不写 0。 -->
    <p v-if="tree !== undefined" class="ara__tree t-meta-read">
      <template v-if="tree">
        {{ t('ratchet.tree.line', { offenders: tree.offenders, lines: tree.excess_lines }) }}
      </template>
      <template v-else>{{ t('ratchet.tree.unknown') }}</template>
    </p>

    <table class="ara__table">
      <thead>
        <tr>
          <th>{{ t('ratchet.table.check') }}</th>
          <th class="num">{{ t('ratchet.table.actual') }}</th>
          <th class="num">{{ t('ratchet.table.frozen') }}</th>
          <th class="num">{{ t('ratchet.table.stale') }}</th>
          <th class="num">{{ t('ratchet.table.change') }}</th>
          <th>{{ t('ratchet.table.verdict') }}</th>
          <th class="ara__spark-col" />
        </tr>
      </thead>
      <tbody>
        <template v-for="check in checks" :key="check.id">
          <tr class="ara__row" :class="{ 'ara__row--open': open.has(check.id) }" @click="toggle(check.id)">
            <td class="ara__name">
              <span>{{ checkName(check.id) }}</span>
              <span class="ara__id mono">{{ check.id }}</span>
              <span v-if="SCOPE_KEYS[check.id]" class="ara__scope">{{ t(SCOPE_KEYS[check.id]!) }}</span>
            </td>

            <!-- 「现在」这一格：没跑到就写「没跑到」，绝不写 0。 -->
            <td class="num">
              <span v-if="number(check.actual) !== null" class="t-num">{{ check.actual }}</span>
              <span v-else class="ara__none" :title="t('ratchet.legend.notCollected')">
                {{ t('ratchet.value.notCollected') }}
              </span>
            </td>

            <td class="num t-num">{{ number(check.frozen) ?? '—' }}</td>

            <td class="num">
              <span v-if="check.stale.length" class="ara__stale t-num">
                {{ t('ratchet.value.staleCount', { count: check.stale.length }) }}
              </span>
              <span v-else class="ara__none">—</span>
            </td>

            <td class="num">
              <template v-if="compare(check)">
                <span class="t-num">{{ compare(check)!.from }}</span>
                <span class="ara__arrow">→</span>
                <span class="t-num">{{ compare(check)!.to }}</span>
                <span class="ara__delta" :class="`ara__delta--${check.direction}`">
                  ({{ compare(check)!.delta > 0 ? '+' : '' }}{{ compare(check)!.delta }})
                </span>
              </template>
              <span
                v-else-if="segment(check).brokenByHole"
                class="ara__none"
                :title="t('ratchet.value.holeBlocksCompareHint')"
              >
                {{ t('ratchet.value.holeBlocksCompare') }}
              </span>
              <span v-else class="ara__none" :title="t('ratchet.value.noSecondPointHint')">
                {{ t('ratchet.value.noSecondPoint') }}
              </span>
            </td>

            <td>
              <span class="ara__verdict" :class="`ara__verdict--${check.direction}`">
                {{ t(DIRECTION_KEYS[check.direction] ?? 'ratchet.direction.unknown') }}
              </span>
              <span v-if="check.points[check.points.length - 1]?.rule_changed" class="ara__mark">
                {{ t('ratchet.mark.ruleChanged') }}
              </span>
            </td>

            <td class="ara__spark-col">
              <AdminRatchetSparkline :points="check.points" :direction="check.direction" />
            </td>
          </tr>

          <tr v-if="open.has(check.id)" class="ara__detail">
            <td colspan="7">
              <dl class="ara__props">
                <dt>{{ t('ratchet.detail.fingerprint') }}</dt>
                <dd>
                  <span class="mono">{{ check.rule_fingerprint?.slice(0, 12) ?? '—' }}</span>
                </dd>
                <template v-if="reasonOf(check)">
                  <dt>{{ t('ratchet.detail.reason') }}</dt>
                  <dd>{{ reasonOf(check) }}</dd>
                </template>
              </dl>

              <div v-if="check.stale.length" class="ara__block">
                <p class="ara__block-title">{{ t('ratchet.detail.stale') }}</p>
                <ul class="ara__list">
                  <li v-for="(entry, i) in check.stale" :key="i" class="mono">{{ staleText(entry) }}</li>
                </ul>
              </div>

              <div class="ara__block">
                <p class="ara__block-title">{{ t('ratchet.detail.timeline') }}</p>
                <table class="ara__inner">
                  <thead>
                    <tr>
                      <th>{{ t('ratchet.timeline.when') }}</th>
                      <th>{{ t('ratchet.timeline.commit') }}</th>
                      <th class="num">{{ t('ratchet.table.actual') }}</th>
                      <th class="num">{{ t('ratchet.table.frozen') }}</th>
                      <th>{{ t('ratchet.timeline.marks') }}</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="point in check.points" :key="point.commit + point.collected_at">
                      <td class="mono">{{ stamp(point.collected_at) }}</td>
                      <td>
                        <a class="ara__link mono" :href="point.run_url" target="_blank" rel="noopener">
                          {{ short(point.commit) }}
                        </a>
                      </td>
                      <td class="num">
                        <span v-if="point.actual !== null" class="t-num">{{ point.actual }}</span>
                        <span v-else class="ara__none">{{ t('ratchet.value.notCollected') }}</span>
                      </td>
                      <td class="num t-num">{{ point.frozen ?? '—' }}</td>
                      <td>
                        <span v-if="point.collection !== 'ok'" class="ara__mark ara__mark--bad">
                          {{ t('ratchet.mark.collectionFailed') }}
                        </span>
                        <span v-if="point.rule_changed" class="ara__mark">{{ t('ratchet.mark.ruleStarted') }}</span>
                        <span v-if="point.new_exemptions" class="ara__mark ara__mark--warn">
                          {{ t('ratchet.mark.newExemptions', { count: point.new_exemptions }) }}
                        </span>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>

              <div class="ara__block">
                <p class="ara__block-title">{{ t('ratchet.detail.details') }}</p>
                <p v-if="!detailsOf(check).length" class="ara__none">{{ t('ratchet.detail.noDetails') }}</p>
                <ul v-else class="ara__list ara__list--scroll">
                  <li v-for="(entry, i) in detailsOf(check)" :key="i">
                    <span class="mono">{{ detailFile(entry) }}</span>
                    <span class="ara__extra mono">{{ extra(entry) }}</span>
                  </li>
                </ul>
              </div>
            </td>
          </tr>
        </template>
      </tbody>
    </table>
  </section>
</template>

<style scoped>
/* 提交、指纹、文件名一律等宽：这一页上「一模一样」和「差一个字符」的区别全靠它。 */
.mono {
  font-family: var(--font-mono);
}

/* 卡片长相交给 `.admin-card`（`--line` 描边 + `--radius-lg`），这里只留「块与块之间
   隔 16px」和「表角跟着卡片圆角收」。原先这一页自己写了一份 `--line-2` + 8px 圆角，
   和管理台里其他每一张卡片都不一样；`overflow: hidden` 是给表格方角收边的。 */
.ara {
  margin-top: 16px;
  overflow: hidden;
}

.ara__head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 12px 16px 8px;
  flex-wrap: wrap;
}

.ara__title {
  margin: 0;
}

.ara__count {
  color: var(--muted);
}

.ara__table,
.ara__inner {
  width: 100%;
  border-collapse: collapse;
}

.ara__table th,
.ara__table td,
.ara__inner th,
.ara__inner td {
  padding: 8px 12px;
  border-top: 1px solid var(--line);
  font-size: 13px;
  text-align: left;
  vertical-align: middle;
}

/* 表头长得和 `AdminGrid` 那张表一样：surface 底、下面一道 `--line-2`、12px 600 的
   `--muted`。原来这里是**一块填色**（`--fill`）+ 500 字重 —— 全后台只有这一张表把
   表头画成一条灰带子，切分区时像是另一个产品里的表。 */
.ara__table thead th,
.ara__inner thead th {
  font-size: 12px;
  font-weight: 600;
  color: var(--muted);
  background: var(--surface);
  border-top: 0;
  border-bottom: 1px solid var(--line-2);
  white-space: nowrap;
}

/* 表头那条线已经分隔了第一行，第一行自己不画（画了就是两条贴在一起）。 */
.ara__table tbody tr:first-child td,
.ara__inner tbody tr:first-child td {
  border-top: 0;
}

.ara__table tbody tr:last-child td {
  border-bottom: 0;
}

.ara__row {
  cursor: pointer;
}

.ara__row:hover td {
  background: var(--fill);
}

.ara__name {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

/* 检查的英文 id 是**读得出来**的一行字（有人要拿它去 grep 脚本），不是装饰，所以它
   跟正文同档的下限 12px，不再往 11px 掉 —— 设计系统里手写字号只有 12 起。 */
.ara__id {
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}

/* 口径提示：只有 `file-sizes` 带。它是这一页唯一一个 `actual` 不是树级测量的格子，
   这句话就挂在那个数旁边。 */
.ara__scope {
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--faint);
}

/* 树级数字那一行。它是这个区的**第一句结论**，所以比表头符重一档、比正文轻一档。 */
.ara__tree {
  margin: 0;
  padding: 0 16px 8px;
  color: var(--muted);
}

.ara__spark-col {
  width: 104px;
}

.ara__none {
  color: var(--muted);
  font-size: 12px;
}

.ara__stale {
  color: var(--warn-ink);
}

.ara__arrow {
  margin: 0 4px;
  color: var(--muted);
}

.ara__delta {
  margin-left: 4px;
  font-size: 12px;
}

.ara__delta--improving {
  color: var(--ok-ink);
}

.ara__delta--worse {
  color: var(--danger-ink);
}

.ara__delta--flat,
.ara__delta--unknown {
  color: var(--muted);
}

.ara__verdict--improving {
  color: var(--ok-ink);
}

.ara__verdict--worse {
  color: var(--danger-ink);
}

.ara__verdict--flat,
.ara__verdict--unknown {
  color: var(--muted);
}

.ara__mark {
  display: inline-block;
  margin-left: 6px;
  padding: 0 6px;
  border-radius: var(--radius-sm);
  font-size: 12px;
  line-height: 18px;
  background: var(--fill-2);
  color: var(--accent-ink);
  white-space: nowrap;
}

.ara__mark--warn {
  background: var(--warn-wash);
  color: var(--warn-ink);
}

.ara__mark--bad {
  background: var(--danger-wash);
  color: var(--danger-ink);
}

.ara__detail td {
  padding: 12px 16px 16px;
  background: var(--fill);
}

.ara__props {
  display: grid;
  grid-template-columns: 96px 1fr;
  gap: 4px 12px;
  margin: 0;
  font-size: 12.5px;
}

.ara__props dt {
  color: var(--muted);
}

.ara__props dd {
  margin: 0;
}

.ara__block {
  margin-top: 14px;
}

.ara__block-title {
  margin: 0 0 6px;
  font-size: 12px;
  color: var(--muted);
}

.ara__list {
  margin: 0;
  padding: 0;
  list-style: none;
  font-size: 12.5px;
}

.ara__list--scroll {
  max-height: 320px;
  overflow-y: auto;
}

.ara__list li {
  display: flex;
  gap: 8px;
  padding: 2px 0;
  flex-wrap: wrap;
}

.ara__extra {
  color: var(--muted);
}

.ara__link {
  color: var(--accent-ink);
  text-decoration: none;
}
</style>
