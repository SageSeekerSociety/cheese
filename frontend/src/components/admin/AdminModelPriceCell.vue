<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

// 「模型」表里那一格单价。
//
// 两点是这一格的读者真正要的，所以都不交给调用方：
//
//   1. **原始数字换成人读的量纲**。网关账本按**每 token** 存价，量级是 4.2e-7
//      这种 —— 直接印出来没人比得出两个模型谁贵。换算成「每百万 token 的美元」，
//      这一列才是可比较的数（这也是各家价目表在用的口径）。
//   2. **没价时说的是「未定价」，不是 `$0`**。0 是「确实免费」，没价是「算不出钱」，
//      两者在这套产品里含义相反（`usageFormat.ts` 的 `costLabel` 同一个道理）。
//      而且没价是有原因的（缺输入或输出单价），原因由服务端下发，这一格**照原话给出**，
//      不另写一句「未定价」把信息丢掉。
//
// 上架开关要不要灰掉由**页面**按 `priced` 判，不在这里 —— 这一格只管把价画出来。

/** 缺的键不出现（契约 §2.1 的 `prices`）。`null` 与缺省同义：没这一档价。 */
interface Prices {
  input?: number | null
  output?: number | null
  cache_read?: number | null
  cache_creation?: number | null
}

const props = defineProps<{
  /** 双向都有价（input 与 output 都 > 0）才是 true。 */
  priced: boolean
  prices: Prices
  /** 没定价时服务端给的人话原因。`priced=false` 时显示。 */
  reason?: string | null
}>()

const { t } = useI18n()

/** 每 token → 每百万 token 的美元。空值给空串，交给模板决定画不画。 */
function perMillion(v: number | null | undefined): string {
  if (v === null || v === undefined) return ''
  return `$${(v * 1_000_000).toFixed(2)}`
}

const input = computed(() => perMillion(props.prices.input))
const output = computed(() => perMillion(props.prices.output))
</script>

<template>
  <div v-if="priced" class="ampc">
    <!-- 入 / 出 两行而不是一行：两档价挤成一行，读者要自己数逗号才分得清哪个是哪个。 -->
    <span class="ampc__row">
      <span class="ampc__tag t-meta-read">{{ t('models.price.input') }}</span>
      <span class="ampc__num t-num">{{ input }}</span>
    </span>
    <span class="ampc__row">
      <span class="ampc__tag t-meta-read">{{ t('models.price.output') }}</span>
      <span class="ampc__num t-num">{{ output }}</span>
    </span>
  </div>

  <!-- 未定价：主字是「未定价」，原因是第二行（截断，完整那句挂 title）。 -->
  <div v-else class="ampc">
    <span class="ampc__unpriced">{{ t('models.price.unpriced') }}</span>
    <span v-if="reason" class="ampc__reason t-meta-read" :title="reason">{{ reason }}</span>
  </div>
</template>

<style scoped>
.ampc {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.ampc__row {
  display: flex;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
}

/* 「入」「出」是这一行的引导词，用最淡的一档 —— 它只在两行之间做区分，本身不承载信息。 */
.ampc__tag {
  flex: 0 0 auto;
  color: var(--muted);
}

.ampc__num {
  overflow: hidden;
  color: var(--ink);
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 「未定价」用 `--muted` 而不是 `--warn-ink`：缺价是一个**状态事实**，不是一条警告 ——
 *  这一列里灰掉的项和定价的项是同一档信息，染成琥珀会把整列的重点带偏（design-system §1.6）。 */
.ampc__unpriced {
  color: var(--muted);
}

.ampc__reason {
  overflow: hidden;
  color: var(--faint);
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
