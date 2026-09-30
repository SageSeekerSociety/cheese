<script setup lang="ts">
import type { CallbackNotice, GithubAccountLoadState } from '@/composables/useProjectSettings'
import type { OAuthConnectionInfo } from '@/cx_types'

import { isGithubAccountTokenExpired } from '@/lib/githubAccount'
import { relTime } from '@/lib/relTime'

// 连接 GitHub 账号 (#192)：App 的 user-to-server 授权，独立于登录用的经典 OAuth
// —— 记录「这个人是哪个 GitHub 账号」，供 credit 归属 + 两阶段采纳代表身份开 PR 用。
// 拆自 `views/ProjectSettingsView.vue`（#2143）。
//
// 四态是**这一块自己的**（和页面的 loading 分开）：读失败绝不能画成「未连接」，那是
// 一句假话 —— 所以失败那一档和「未连接」那一档长得不一样，各自都有一句话。
//
// 结果只报在自己这一块（`notice` 是账号流程那一条，不是仓库那一条）：两条流程各有
// 各的按钮，一次动作的结果出现在另一件事的标题下面就说不清是哪一次了。
defineOptions({ name: 'GithubAccountSettings' })

defineProps<{
  state: GithubAccountLoadState
  loadError: string | null
  /** 绑定着的那个 GitHub 账号；`state === 'loaded'` 时才有（没连就是 null）。 */
  conn: OAuthConnectionInfo | null
  /** 正在拿授权链接 / 正在断开。 */
  connecting: boolean
  disconnecting: boolean
  /** 账号流程那一条结果。 */
  notice: CallbackNotice | null
}>()

const emit = defineEmits<{
  retry: []
  connect: []
  disconnect: []
  'clear-notice': []
}>()
</script>

<template>
  <section class="page-section">
    <div class="page-section-head">
      <v-icon size="14" class="c-faint">mdi-account-box-outline</v-icon>
      <span class="page-section-title">连接 GitHub 账号</span>
    </div>
    <div class="page-section-body">
      <!-- The 账号 flow's own outcome, in the 账号 section. -->
      <v-alert
        v-if="notice"
        :type="notice.type"
        density="comfortable"
        closable
        class="mb-3"
        @click:close="emit('clear-notice')"
      >
        {{ notice.text }}
      </v-alert>

      <!-- 加载中 -->
      <div v-if="state === 'loading'" class="d-flex align-center" style="gap: 8px">
        <v-progress-circular indeterminate size="16" width="2" color="primary" />
        <span class="t-body c-muted">正在加载连接状态…</span>
      </div>

      <!-- 请求失败: never fall through to the "未连接" look, that would lie -->
      <div v-else-if="state === 'error'" class="d-flex align-center flex-wrap" style="gap: 8px">
        <v-icon size="18" color="error">mdi-alert-circle-outline</v-icon>
        <span class="t-body text-error settings-row-label">{{ loadError ?? '加载连接状态失败' }}</span>
        <v-spacer />
        <v-btn size="small" variant="text" @click="emit('retry')">重试</v-btn>
      </div>

      <!-- 已连接 -->
      <template v-else-if="conn">
        <div class="d-flex align-center flex-wrap" style="gap: 8px">
          <v-icon size="18" color="success">mdi-check-circle</v-icon>
          <span class="t-body settings-row-label">
            已连接 <strong>{{ conn.login ?? conn.providerUserId }}</strong>
            <span v-if="conn.connectedAt" class="c-faint settings-hint"> （{{ relTime(conn.connectedAt) }}连接） </span>
          </span>
          <v-spacer />
          <v-btn size="small" variant="tonal" :loading="connecting" @click="emit('connect')"> 重新连接 </v-btn>
          <v-btn size="small" variant="text" color="error" :loading="disconnecting" @click="emit('disconnect')">
            断开
          </v-btn>
        </div>
        <v-alert
          v-if="isGithubAccountTokenExpired(conn)"
          type="warning"
          density="comfortable"
          variant="tonal"
          class="mt-2"
        >
          GitHub 授权已过期，暂时无法以你的身份创建 PR。请点击“重新连接”刷新授权。
        </v-alert>
      </template>

      <!-- 未连接 -->
      <div v-else class="d-flex align-center flex-wrap" style="gap: 8px">
        <span class="t-body c-muted">暂无关联账号</span>
        <v-spacer />
        <v-btn size="small" color="primary" variant="tonal" :loading="connecting" @click="emit('connect')">
          连接 GitHub 账号
        </v-btn>
      </div>

      <p class="t-body c-faint mt-2 settings-hint">
        用于识别你的提交署名，并以你的身份创建 GitHub PR。这与登录用的 GitHub 授权相互独立，可以连接不同的账号。
      </p>
    </div>
  </section>
</template>

<style scoped src="./settings-section.css"></style>
