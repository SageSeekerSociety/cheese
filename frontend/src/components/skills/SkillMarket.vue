<script setup lang="ts">
// 技能市场：两家厂商公开的技能 / 插件 / MCP，页签照 Claude Desktop 的 Discover
// ——每家一张卡片栅格；卡片上的 + 就是「从网上取一份到这台机器上」。第四个货架
// （Agent 模板）眼下只是个入口：后台还没有这类东西，就照实说一句「还没有」，
// 不接接口、不摆假数据。
//
// 只画、只往上发：目录、机器索引、落盘都在页面里（队友技能面板 / 全局市场页）。
// 这段 UI 原来长在 AgentSkillsDialog 里，抽出来两处共用，弹窗那边外观与交互不变。
//
// 公开目录，和哪台机器无关；但「装」要有个落点，所以没机器时 + 按不动 —— 组件如实
// 禁用它，由页面在外面说清「先连接一台机器」。
//
// 按组件规矩不搬 API 层的类型（scripts/import-boundary-ratchet-core.mjs）：这里只用
// 结构相同的本地声明。
import { computed } from 'vue'

import { t } from '@/i18n'
import { skillIcon } from '@/lib/skillIcons'

// 市场的一张卡片与整份清单。和 API 层结构相同，但按组件规矩不搬那边的类型。
interface MarketCardItem {
  name: string
  description: string
  url: string
  source: string
  kind: 'skill' | 'plugin' | 'mcp'
}
interface MarketItem {
  skills: MarketCardItem[]
  plugins: MarketCardItem[]
  plugin_total: number
  mcp: MarketCardItem[]
  notes: string[]
}
const props = withDefaults(
  defineProps<{
    market?: MarketItem | null
    loading?: boolean
    /** 装到哪台机器上。没有 = 禁掉 +，卡片刻画照旧。 */
    deviceId?: string | null
    scanning?: boolean
    saving?: boolean
    /** 要不要摆第四个货架「Agent 模板」。只在**市场**里摆：队友技能面板那一格问的是
     *  「这个队友带哪些技能」，模板不属于那一问。 */
    showAgents?: boolean
  }>(),
  { market: null, loading: false, deviceId: null, scanning: false, saving: false, showAgents: false }
)

const emit = defineEmits<{
  (e: 'install', payload: { url: string; kind: 'skill' | 'plugin' }): void
}>()

// 页签照 Claude Desktop 的 Discover：技能 / 插件 / MCP，外加第四个货架「Agent 模板」。
// 前三个的货品来自公开目录（market），第四个只是个入口 —— 后台还没有这类东西，所以
// 它不读 market、不摆卡片，只照实说一句「还没有」（见下面 cards 与模板里那一支）。
type MarketTab = 'skills' | 'plugins' | 'mcp' | 'agents'
const TABS: { kind: MarketTab; label: string }[] = [
  { kind: 'skills', label: 'work.room.skills.marketTabSkills' },
  { kind: 'plugins', label: 'work.room.skills.marketTabPlugins' },
  { kind: 'mcp', label: 'work.room.skills.marketTabMcp' },
  { kind: 'agents', label: 'work.room.skills.marketTabAgents' },
]

// 摆哪几格：第四个货架（Agent 模板）只在市场里摆，队友技能面板不摆。
const tabs = computed(() => (props.showAgents ? TABS : TABS.filter((tab) => tab.kind !== 'agents')))
// 停在哪个页签。用 model 而不是内部 ref：队友技能面板要在每次打开时把它复位，
// 全局市场页没人绑它，就地自己管。
const marketTab = defineModel<MarketTab>('tab', { default: 'skills' })

// 当前页签要画的卡片。「Agent 模板」那格没有货品，永远空 —— 不拿去 index market，
// 免得为它编一份数据。
const cards = computed(() => {
  const tab = marketTab.value
  if (!props.market || tab === 'agents') return []
  return props.market[tab]
})
</script>

<template>
  <!-- 市场：中性底、正文色（不借任何琥珀）。页签 —— 技能 / 插件 / MCP，外加第四个
       货架「Agent 模板」—— 每家一张卡片栅格；卡片上的 + 就是「从网上取一份到这台
       机器上」。第四个货架只是个入口，没有货品（见下）。 -->
  <section class="market">
    <div class="market__head">
      <span class="market__title t-eyebrow-read">{{ t('work.room.skills.marketTitle') }}</span>
      <div class="market__tabs t-meta-read" role="tablist">
        <button
          v-for="tab in tabs"
          :key="tab.kind"
          type="button"
          role="tab"
          class="market__tab"
          :class="{ 'market__tab--on': marketTab === tab.kind }"
          :aria-selected="marketTab === tab.kind"
          @click="marketTab = tab.kind"
        >
          {{ t(tab.label) }}
        </button>
      </div>
    </div>

    <!-- 第四个货架：只有入口。后台还没有 Agent 模板这类东西，就不接接口、不摆假
         数据，照实说一句「还没有」。 -->
    <p v-if="marketTab === 'agents'" class="market__note t-meta-read">
      {{ t('work.room.skills.marketTabAgentsEmpty') }}
    </p>
    <p v-else-if="loading" class="market__note t-meta-read">{{ t('work.room.skills.marketLoading') }}</p>
    <p v-else-if="!market" class="market__note t-meta-read">{{ t('work.room.skills.marketFailed') }}</p>
    <template v-else>
      <p v-if="!cards.length" class="market__note t-meta-read">
        {{ t('work.room.skills.marketEmpty') }}
      </p>
      <p v-else-if="marketTab === 'plugins'" class="market__note t-meta-read">
        {{ t('work.room.skills.marketPluginTotal', { shown: cards.length, total: market.plugin_total }) }}
      </p>
      <div class="market__grid">
        <article v-for="card in cards" :key="`${card.kind}:${card.name}`" class="mcard">
          <span class="mcard__icon"
            ><v-icon size="18">{{ skillIcon(card.name) }}</v-icon></span
          >
          <span class="mcard__body">
            <span class="mcard__name t-body">{{ card.name }}</span>
            <span class="mcard__desc t-meta-read">{{ card.description }}</span>
            <span class="mcard__from t-meta-read">{{ t(`work.room.skills.marketSource.${card.source}`) }}</span>
          </span>
          <!-- MCP server 不是一颗技能，只有展示；插件和技能各有自己的 +。 -->
          <button
            v-if="card.kind !== 'mcp' && card.url !== ''"
            type="button"
            class="mcard__add"
            :disabled="scanning || !deviceId || saving"
            :aria-label="t('work.room.skills.marketInstall')"
            :title="deviceId ? t('work.room.skills.marketInstall') : t('work.room.skills.noMachine')"
            @click="emit('install', { url: card.url, kind: card.kind as 'skill' | 'plugin' })"
          >
            <v-icon size="18">mdi-plus</v-icon>
          </button>
          <span v-else-if="card.kind === 'mcp'" class="mcard__from t-meta-read">{{
            t('work.room.skills.marketNoInstall')
          }}</span>
        </article>
      </div>
      <p v-if="market.notes.length" class="market__note t-meta-read">{{ market.notes.join('；') }}</p>
    </template>
  </section>
</template>

<style scoped>
/* 市场：中性底 + 正文色，两种主题各自成立（深色是深底浅字、浅色是白底深字），所以
   不写死颜色，也不碰任何 amber 变量。 */
.market {
  margin-top: 12px;
  padding: 14px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--surface);
  color: var(--text);
}
.market__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 12px;
  margin-bottom: 10px;
}
/* 页签的字挂在这一层而不是按钮上：style.css 的裸 button 重置
   （button:not(.v-btn){font:inherit}，特指度 0,1,1）压得过单类阶梯类（0,1,0），
   按钮自己挂 .t-meta-read 会被它按回继承值（16px）。放在容器上，按钮 font:inherit
   正好接住。 */
.market__tabs {
  display: flex;
  gap: 4px;
  margin-inline-start: auto;
}
.market__tab {
  padding: 4px 10px;
  border: 0;
  border-radius: var(--radius-pill);
  background: transparent;
  cursor: pointer;
}
.market__tab--on {
  background: var(--fill);
  font-weight: 600;
}
.market__note {
  margin: 0 0 8px;
}
.market__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
  gap: 8px;
}
.mcard {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 8px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}
.mcard__icon {
  display: grid;
  flex: none;
  place-items: center;
  width: 30px;
  height: 30px;
  border-radius: var(--radius-md);
  background: var(--fill);
  color: var(--faint);
}
.mcard__body {
  flex: 1;
  min-width: 0;
}
.mcard__name {
  display: block;
}
.mcard__desc {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.mcard__from {
  flex: none;
}
/* 卡片上的 +：装到这台机器上。没机器时按不动（落盘总得有个落点）。 */
.mcard__add {
  display: grid;
  flex: none;
  place-items: center;
  width: 28px;
  height: 28px;
  border: 0;
  border-radius: var(--radius-md);
  background: var(--fill);
  color: var(--text);
  cursor: pointer;
}
.mcard__add:hover:not(:disabled) {
  background: var(--fill-2);
}
.mcard__add:disabled {
  cursor: default;
  opacity: 0.5;
}
</style>
