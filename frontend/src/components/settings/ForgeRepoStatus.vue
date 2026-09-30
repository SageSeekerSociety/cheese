<script setup lang="ts">
import type { ForgeConnection } from '@/cx_types'

// 由芝士托管的项目（forgejo）的「代码仓库」那一块：一行状态加一颗「打开仓库」。
// 拆自 `views/ProjectSettingsView.vue`（#2143）。
//
// 托管服务是建项目时定下来的，所以这一块没有可编的东西：它只把当前状态画出来。
defineOptions({ name: 'ForgeRepoStatus' })

defineProps<{
  /** `kind === 'forgejo'` 的那份仓库状态。 */
  forge: ForgeConnection
}>()
</script>

<template>
  <section class="page-section" data-testid="forge-repository">
    <div class="page-section-head">
      <v-icon size="14" class="c-faint">mdi-source-repository</v-icon>
      <span class="page-section-title">代码仓库</span>
    </div>
    <div class="page-section-body">
      <div class="d-flex align-center flex-wrap" style="gap: 8px">
        <v-icon v-if="forge.connected" size="18" color="success">mdi-check-circle</v-icon>
        <span class="t-body">{{ forge.connected ? '由芝士托管' : '仓库正在准备中' }}</span>
        <v-spacer />
        <v-btn
          v-if="forge.url"
          :href="forge.url"
          target="_blank"
          rel="noopener noreferrer"
          size="small"
          variant="tonal"
        >
          打开仓库
        </v-btn>
      </div>
      <p class="t-body c-faint mt-2 settings-hint">项目创建后，暂不支持切换托管服务。</p>
    </div>
  </section>
</template>

<style scoped src="./settings-section.css"></style>
