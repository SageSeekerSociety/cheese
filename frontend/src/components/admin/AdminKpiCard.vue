<script setup lang="ts">
import type { RouteLocationRaw } from 'vue-router'

import { computed } from 'vue'

import AdminNoteTip from '@/components/admin/AdminNoteTip.vue'
import AdminSparkline from '@/components/admin/AdminSparkline.vue'

// 看板 KPI 行里的一张卡（92px，有第三行时 108px）。
//
// **有没有 `to` 决定这张卡是不是一个可点的东西**，而且两种卡差的不是「像不像链接」，
// 是可点的那三件实事：`cursor: pointer`、hover 底色、进 Tab 顺序。只给它一半 —— 指针
// 变了形、指针放上去有反应，按下去却什么都不发生 —— 比完全不给更糟：人下一次就不再
// 相信这一屏里任何指针的含义了。所以不可点的那张这三样一样都不写（不是「写了但看不
// 出来」，是根本不写）。
//
// 数字由调用方格式化好再进来（`value` 是字符串）：千分位、「12.4 万」这类写法是看板
// 的口径，不是一张卡的口径。这里只负责把它画成 23px / `.t-num` 那一套（§6.1 的
// `.t-console-title`，它定义的一处用处就是「看板 KPI 大数字」）。
//
// `value` 是空串时画长破折号，不画 0：0 是**读出来了、这周确实是零**，空串是**没读到**。
// 两者在屏幕上必须长得不一样，否则「接口挂了」会被读成「一切正常，只是没人提交」。
//
// 第三行（`delta` / `spark`）是**趋势层**：delta 是环比差（上一等长窗口合计由后端
// `prev` 字段给），spark 是窗口内逐日的形状。两个约束：
//
//   * **delta 中性呈现**（`--muted`，不用红绿）：「待分诊 +12%」是好事还是坏事取决于
//     指标本身，方向语义由 `deltaTitle` 那句话承担，不由颜色说。
//   * **`note` 只在无 `to` 时生效**：有 `to` 的卡整卡是一个 router-link，链接里套
//     按钮是嵌套交互，不允许 —— 所以模板里是 `v-if="note && !to"`。

const props = withDefaults(
  defineProps<{
    /** 「待分诊」这类短标签。 */
    label: string
    /** 已格式化好的数字串。空串 = 拿不到值。 */
    value: string
    /** 可选后缀，如「条」。 */
    unit?: string
    /** 有去向才是链接。见文件开头。 */
    to?: RouteLocationRaw
    /** 首次加载。骨架的形状和真卡完全一样，到货那一刻不重排。 */
    loading?: boolean
    /** 已格式化的环比（「+12.3%」「-4%」）；空串/undefined = 不画。 */
    delta?: string
    /** delta 的口径句，挂 title（「上一周期（再前 7 天）：1,024」）。 */
    deltaTitle?: string
    /** 迷你折线的逐日值；null 断段；全 null 或空数组不画。**传了数组（哪怕是空的）
     *  这张卡就有第三行** —— 调用方在数据没到齐时传 `?? []`，骨架才能和真卡同形
     *  （到货不重排）；`undefined` 才是「这张卡没有第三行」。 */
    spark?: (number | null)[]
    /** 口径注。给了才在 label 旁画 info tip —— **只在无 `to` 时生效**（见文件头）。 */
    note?: string
  }>(),
  { loading: false }
)

const shown = computed(() => (props.value.trim() === '' ? '—' : props.value))

/** 有没有第三行 —— 决定高度档（92 → 108）。grid 行默认 stretch，同一排里
 *  有第三行和没有第三行的卡自动同高。 */
const hasThirdRow = computed(() => Boolean(props.delta) || props.spark !== undefined)
</script>

<template>
  <!-- 骨架画成 `<div>` 而不是链接：一张还没到货的卡不是一个目的地，把它放进 Tab 顺序
       等于让人在数字出现之前先 Tab 到它一次。rich 卡多第三条骨头（foot），和真卡的
       形状一致 —— 到货不重排。 -->
  <div v-if="loading" class="akpi" :class="{ 'akpi--rich': hasThirdRow }">
    <v-skeleton-loader type="text" class="akpi__skel akpi__skel--label" />
    <v-skeleton-loader type="text" class="akpi__skel akpi__skel--value" />
    <v-skeleton-loader v-if="hasThirdRow" type="text" class="akpi__skel akpi__skel--foot" />
  </div>

  <router-link v-else-if="to" :to="to" class="akpi akpi__link" :class="{ 'akpi--rich': hasThirdRow }">
    <span class="akpi__head">
      <span class="akpi__label t-eyebrow-read">{{ label }}</span>
    </span>
    <span class="akpi__value">
      <span class="akpi__num t-console-title t-num">{{ shown }}</span>
      <span v-if="unit" class="akpi__unit t-dense">{{ unit }}</span>
    </span>
    <span v-if="hasThirdRow" class="akpi__foot">
      <span v-if="delta" class="akpi__delta t-meta-read t-num num-leaf" :title="deltaTitle">{{ delta }}</span>
      <AdminSparkline v-if="spark?.length" class="akpi__spark" :values="spark" />
    </span>
  </router-link>

  <div v-else class="akpi" :class="{ 'akpi--rich': hasThirdRow }">
    <span class="akpi__head">
      <span class="akpi__label t-eyebrow-read">{{ label }}</span>
      <AdminNoteTip v-if="note" :text="note" />
    </span>
    <span class="akpi__value">
      <span class="akpi__num t-console-title t-num">{{ shown }}</span>
      <span v-if="unit" class="akpi__unit t-dense">{{ unit }}</span>
    </span>
    <span v-if="hasThirdRow" class="akpi__foot">
      <span v-if="delta" class="akpi__delta t-meta-read t-num num-leaf" :title="deltaTitle">{{ delta }}</span>
      <AdminSparkline v-if="spark?.length" class="akpi__spark" :values="spark" />
    </span>
  </div>
</template>

<style scoped>
/* 高度写死、**宽度交给格子**。
   高度 92 是这一行高度算式的前提（§4.2），有第三行（delta/spark）时升 108 ——
   12+18+4+33+4+20+12=103，取 108 对齐 8px 网格余量。宽度一度写死成 263px
   （4 × 263 + 3 × 16 = 1100），那只是**在设计宽度下**成立的等式 —— 而格子本来就是
   `minmax(0, 1fr)`，宽度由它给。写死之后，任何比设计宽度窄的窗口里卡片都**溢出
   自己那一格、压到隔壁**上（真浏览器里量到过：1100px 下两张卡的标题重叠 12px、
   手机上 148px）。`min-width: 0` 是让格子真的收得动：网格项的自动最小尺寸是
   min-content，不写这条，长标题仍然会把格子顶回去。 */
.akpi {
  display: flex;
  flex-direction: column;
  justify-content: center;
  /* 高度预算的构成（92 档 / 108 档）：12 上 padding + 18 label + 4 + 33 大数字
     (+ 4 + 20 第三行) + 12 下 padding = 79 / 103 —— 余量居中吃掉的等式写在上面。 */
  gap: 4px;
  box-sizing: border-box;
  width: 100%;
  min-width: 0;
  height: 92px;
  padding: 12px 16px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-top-left-radius: var(--radius-lg);
  border-top-right-radius: var(--radius-lg);
  border-bottom-right-radius: var(--radius-lg);
  border-bottom-left-radius: var(--radius-lg);
}

.akpi--rich {
  height: 108px;
}

/* 窄屏：数字缩一档。手机上是两列、每列约 150px，23px 的六位数字会把卡片撑破一点点
   （真浏览器里量到过：`204,900` 与隔壁那张的 `55` 交叠 3px）。20px 仍是「大数字」那一档
   （`.t-console-title` 的下一级），但它装得下。两条查询各司其职：@media 管没有容器
   祖先的页面（模型页），@container 管挂在 `.ad__inner`（container-type）下的看板 ——
   视口 824–964px 这一带侧栏吃掉 ~240px，容器里 4 列的卡只剩 ~134–169px，纯视口查询
   在这里失效（真评审抓到的带），所以容器版按容器宽 760 降档：4 列时卡 < ~178px、
   2 列时容器 < ~416px 也一并覆盖。 */
@media (max-width: 600px) {
  .akpi__num {
    font-size: 20px;
  }
}

@container (max-width: 760px) {
  .akpi__num {
    font-size: 20px;
  }
}

/* label 与（可选的）口径 tip 同一行。 */
.akpi__head {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}

.akpi__label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 单位跟着数字的基线坐，不跟行盒：`.t-console-title` 的行高是 33px，单位若按行盒居中
   会掉到数字底部以下，读起来像另一个字段。 */
.akpi__value {
  display: flex;
  align-items: baseline;
  gap: 4px;
  min-width: 0;
}

/* 第三行：环比在左、迷你折线占满余下的宽度。 */
.akpi__foot {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

/* delta 中性呈现（不用红绿）：方向语义由 `deltaTitle` 那句话承担 —— 「待分诊 +12%」
   是好事还是坏事取决于指标本身。 */
.akpi__delta {
  flex: 0 0 auto;
  color: var(--muted);
}

.akpi__spark {
  flex: 1 1 auto;
  min-width: 0;
}

/* 有 `to` 才有的三件事。hover 包在 `(hover: hover)` 里：触屏点过之后 `:hover` 会一直
   粘着，那张卡看起来像被选中了 —— 而这一页没有「选中一张 KPI 卡」这回事。 */
.akpi__link {
  text-decoration: none;
  cursor: pointer;
  transition: background-color 0.12s ease;
}

@media (hover: hover) and (pointer: fine) {
  .akpi__link:hover {
    background: var(--fill);
  }
}

/* 骨架。`v-skeleton-loader` 的骨头默认带 16px 外边距和 12px 高，放在 92px 的卡里会把
   两条挤成一条；改成本地尺寸。底色用 `--fill-2`（§7.1 给骨架条指定的那一档），
   Vuetify 默认的 `--v-theme-on-surface` 在深浅两个主题里都不是这一档。 */
.akpi__skel--label {
  width: 56px;
}

.akpi__skel--value {
  width: 72px;
}

.akpi__skel--foot {
  width: 96px;
}

.akpi__skel :deep(.v-skeleton-loader__text) {
  height: 12px;
  margin: 0;
  background: var(--fill-2);
}

.akpi__skel--value :deep(.v-skeleton-loader__text) {
  height: 23px;
}
</style>
