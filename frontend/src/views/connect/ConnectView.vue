<!-- 设备接入审批页。cheese cli 运行 `cheese link auto-connect`（未登录会先自动登录）时，
     终端打印 <origin>/connect?code=xxx；用户在浏览器用已登录账号打开本页并「批准」，设备即
     绑定到该用户名下（成为设备 owner），随后出现在「我的 Agent」的设备管理里。 -->
<template>
  <div class="connect-wrap">
    <v-card class="connect-card" rounded="xl" elevation="3">
      <div class="connect-hd">
        <v-icon icon="mdi-link-variant" size="28" class="me-2" />
        <span class="connect-title">接入一台设备</span>
      </div>

      <v-card-text>
        <template v-if="!code">
          <v-alert type="error" variant="tonal" density="comfortable">
            链接缺少 <code>code</code> 参数。请回到终端复制完整的批准链接再打开。
          </v-alert>
        </template>

        <template v-else-if="done">
          <div class="connect-center">
            <v-icon icon="mdi-check-circle" color="success" size="64" />
            <p class="mt-3 text-h6">设备已批准</p>
            <p class="text-body-2 text-medium-emphasis">
              回到终端即可，连接会自动继续。你可以在「我的 Agent」里管理这台设备。
            </p>
            <v-btn color="primary" variant="flat" class="mt-4" :to="{ name: 'MyAgents' }"> 前往「我的 Agent」 </v-btn>
          </div>
        </template>

        <template v-else-if="!loggedIn">
          <p class="text-body-2 text-medium-emphasis mb-4">请先用你的账号登录，登录后即可批准这台设备接入。</p>
          <v-btn color="primary" variant="flat" block :to="signInTo">去登录</v-btn>
        </template>

        <template v-else>
          <p class="text-body-2 text-medium-emphasis mb-1">以下账号将成为这台设备的拥有者：</p>
          <div class="connect-user mb-4">
            <v-avatar size="32"><v-img :src="avatar" /></v-avatar>
            <span class="ms-2 font-weight-medium">{{ nickname }}</span>
          </div>
          <p class="text-body-2 text-medium-emphasis mb-4">
            批准后，这台机器就能被你用来运行 agent。只批准你自己发起的接入。
          </p>
          <v-alert v-if="error" type="error" variant="tonal" density="compact" class="mb-4">
            {{ error }}
          </v-alert>
          <v-btn color="primary" variant="flat" block :loading="busy" @click="approve"> 批准这台设备 </v-btn>
        </template>
      </v-card-text>
    </v-card>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'

import { getAvatarUrl } from '@/utils/materials'

import { authFetch } from '@/network/api/connectorFetch'
import AccountService from '@/services/account'

const route = useRoute()
const code = computed(() => (typeof route.query.code === 'string' ? route.query.code : ''))
const loggedIn = computed(() => AccountService._loggedIn.value)
const nickname = computed(() => AccountService._user.value?.nickname ?? '当前账号')
const avatar = computed(() => getAvatarUrl(AccountService._user.value?.avatarId))
// After signing in, come back to this exact approve link.
const signInTo = computed(() => ({
  name: 'SignIn',
  query: { redirect: route.fullPath },
}))

const busy = ref(false)
const done = ref(false)
const error = ref('')

async function approve(): Promise<void> {
  if (!code.value) return
  busy.value = true
  error.value = ''
  try {
    const r = await authFetch(
      '/api/auth/device/approve',
      { method: 'POST', body: JSON.stringify({ code: code.value }) },
      true
    )
    if (!r.ok) {
      const detail = await r.text().catch(() => '')
      throw new Error(detail || `批准失败（${r.status}）`)
    }
    done.value = true
  } catch (e) {
    error.value = e instanceof Error ? e.message : '批准失败，请重试'
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
.connect-wrap {
  min-height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 32px 16px;
}
.connect-card {
  width: 100%;
  max-width: 440px;
  padding: 8px 4px 12px;
}
.connect-hd {
  display: flex;
  align-items: center;
  padding: 20px 20px 4px;
}
.connect-title {
  font-size: 18px;
  font-weight: 700;
}
.connect-center {
  text-align: center;
  padding: 12px 0;
}
.connect-user {
  display: flex;
  align-items: center;
}
</style>
