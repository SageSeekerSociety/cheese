<script setup lang="ts">
// Fusion merge (A5): the original 知是 product surfaces (空间/小队) rendered
// inside OUR shell, authed by the real product login. Proves the merged app
// serves the original product with real accounts, beside our topic/agent world.
import { onMounted, ref } from 'vue'
import {
  listProductSpaces,
  listProductTeams,
  loginProduct,
  productToken,
  type ProductSpace,
  type ProductTeam,
} from '../api'

const authed = ref(!!productToken())
const username = ref('')
const password = ref('')
const busy = ref(false)
const error = ref<string | null>(null)

const spaces = ref<ProductSpace[]>([])
const teams = ref<ProductTeam[]>([])

async function load() {
  try {
    spaces.value = (await listProductSpaces()).spaces
    teams.value = (await listProductTeams()).teams
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载失败'
  }
}

async function doLogin() {
  if (busy.value) return
  busy.value = true
  error.value = null
  try {
    const { accessToken, user } = await loginProduct(username.value.trim(), password.value)
    localStorage.setItem('accessToken', accessToken)
    localStorage.setItem('user', JSON.stringify(user))
    authed.value = true
    await load()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '登录失败'
  } finally {
    busy.value = false
  }
}

onMounted(() => {
  if (authed.value) load()
})
</script>

<template>
  <div class="product-page">
    <header class="product-head">
      <h1 class="t-title">原版知是 · 空间与小队</h1>
      <p class="t-meta c-muted">
        原 cheese-backend-py 产品面，真实账号登录，合并代码库内直供数据。
      </p>
    </header>

    <!-- Real product login (username/password against /users/auth/login) -->
    <v-card v-if="!authed" rounded="lg" class="login-card pa-5" elevation="1">
      <div class="t-title mb-3">用原版账号登录</div>
      <v-text-field
        v-model="username"
        label="用户名"
        density="compact"
        variant="outlined"
        hide-details
        class="mb-3"
      />
      <v-text-field
        v-model="password"
        label="密码"
        type="password"
        density="compact"
        variant="outlined"
        hide-details
        class="mb-3"
        @keydown.enter="doLogin"
      />
      <v-btn color="primary" variant="flat" :loading="busy" block @click="doLogin">
        登录
      </v-btn>
      <div v-if="error" class="t-meta mt-3" style="color: var(--danger)">{{ error }}</div>
    </v-card>

    <template v-else>
      <div v-if="error" class="t-meta mb-3" style="color: var(--danger)">{{ error }}</div>

      <section class="mb-6">
        <div class="t-eyebrow mb-2">空间（机构）</div>
        <div class="grid">
          <v-card
            v-for="s in spaces"
            :key="s.id"
            rounded="lg"
            class="pa-4"
            variant="outlined"
          >
            <div class="t-body" style="font-weight: 600; color: var(--ink)">
              {{ s.name }}
            </div>
            <div class="t-meta c-muted mt-1">{{ s.intro || s.description }}</div>
          </v-card>
        </div>
      </section>

      <section>
        <div class="t-eyebrow mb-2">小队</div>
        <div class="grid">
          <v-card
            v-for="t in teams"
            :key="t.id"
            rounded="lg"
            class="pa-4"
            variant="outlined"
          >
            <div class="t-body" style="font-weight: 600; color: var(--ink)">
              {{ t.name }}
            </div>
            <div class="t-meta c-muted mt-1">{{ t.intro }}</div>
          </v-card>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.product-page {
  height: 100%;
  overflow-y: auto;
  padding: 28px 32px;
  max-width: 1100px;
}
.product-head {
  margin-bottom: 24px;
}
.login-card {
  max-width: 360px;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 12px;
}
</style>
