<script setup lang="ts">
// 市场（视图）：只吃 props、只往外 emit。取数在同目录的 MarketView（容器）里：技能市场的
// 目录与「我有没有机器」开页就读。
//
// 市场是找东西、装东西的地方，只放能装的：技能、插件、MCP、Agent 模板
// （components/skills/SkillMarket，和队友技能面板同一件）。目录公开，装要一台机器。
// 模型和环境是项目的配置，不是装的东西：在项目设置里选（默认模型、「环境」那一栏），
// 每种沙箱规格是什么、怎么收费也写在那里（EnvironmentPicker）。题目在空间里发布和应征。
//
// The page runs on AppPage. `wide` (`--page-w-wide`, 1100) is the tier for this
// kind of multi-column content — card grids — and gives the page the shared
// header (docs/design-system.md §3.5). `read` would be a narrower, single-column tier.
import type { SkillMarket as SkillMarketData } from '@/api/machineSkills'

import { useI18n } from 'vue-i18n'

import AppPage from '@/components/common/AppPage.vue'
import SkillMarket from '@/components/skills/SkillMarket.vue'

withDefaults(
  defineProps<{
    /** 技能市场的目录（公开，与机器无关）。 */
    market?: SkillMarketData | null
    marketLoading?: boolean
    /** 技能市场的目录与机器都读过了：在那之前不急着说「先连接一台机器」。 */
    marketLoaded?: boolean
    /** 装到哪台机器上。没有 = 卡片上的 + 按不动。 */
    deviceId?: string | null
    scanning?: boolean
  }>(),
  {
    market: null,
    marketLoading: false,
    marketLoaded: false,
    deviceId: null,
    scanning: false,
  }
)
const emit = defineEmits<{
  install: [payload: { url: string; kind: 'skill' | 'plugin' }]
}>()

const { t } = useI18n()
</script>

<template>
  <AppPage :title="t('market.title')" width="wide">
    <!-- 目录公开，没机器也能看；「装」要一台机器，所以没机器时先把话说在前面，卡片上的 +
         由组件如实禁掉。点击加号后会校验并自动安装。 -->
    <p v-if="marketLoaded && !deviceId" class="t-body c-muted mb-5">
      {{ t('work.room.skills.marketNeedMachine') }}
    </p>
    <SkillMarket
      :market="market"
      :loading="marketLoading"
      :device-id="deviceId"
      :scanning="scanning"
      show-agents
      @install="emit('install', $event)"
    />
  </AppPage>
</template>
