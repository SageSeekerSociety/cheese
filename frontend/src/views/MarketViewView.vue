<script setup lang="ts">
// 市场（视图）：两张页签的画面，只吃 props、只往外 emit。取数在同目录的 MarketView
// （容器）里：技能市场的目录与「我有没有机器」开页就读。
//
// 市场是找东西、装东西的地方：
//   技能与插件 — the skill/plugin/MCP/Agent-template market
//   (components/skills/SkillMarket), shared with 队友技能面板. 目录公开，装要一台机器。
//   题目匹配 — Spaces publish 题目 (Task Templates), teams apply with a project.
// 模型和环境是项目的配置，不是装的东西：在项目设置里选（默认模型、「环境」那一栏），
// 每种沙箱规格是什么、怎么收费也写在那里（EnvironmentPicker）。
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

const tab = defineModel<'skills' | 'tasks'>('tab', { default: 'skills' })
</script>

<template>
  <AppPage :title="t('market.title')" width="wide">
    <v-tabs v-model="tab" density="comfortable" color="primary" class="mb-5">
      <v-tab value="skills">
        <v-icon size="18" class="me-2">mdi-storefront-outline</v-icon>{{ t('market.tabs.skills') }}
      </v-tab>
      <v-tab value="tasks">
        <v-icon size="18" class="me-2">mdi-handshake-outline</v-icon>{{ t('market.tabs.tasks') }}
      </v-tab>
    </v-tabs>

    <v-window v-model="tab">
      <!-- 技能与插件：技能 / 插件 / MCP 的市场（components/skills/SkillMarket 画）。目录
           公开，没机器也能看；「装」要一台机器，所以没机器时先把话说在前面，卡片上的 +
           由组件如实禁掉。点击加号后会校验并自动安装。 -->
      <v-window-item value="skills">
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
      </v-window-item>

      <!-- Challenge matching: challenges published by Spaces, teams apply (spec §13 stage 6) -->
      <v-window-item value="tasks">
        <i18n-t scope="global" keypath="market.tasksIntro" tag="p" class="t-body c-muted mb-5" style="max-width: 660px">
          <template #challenges>
            <strong>{{ t('market.tasksIntroChallenges') }}</strong>
          </template>
          <template #apply>
            <strong>{{ t('market.tasksIntroApply') }}</strong>
          </template>
        </i18n-t>
      </v-window-item>
    </v-window>
  </AppPage>
</template>
