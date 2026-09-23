<template>
  <v-dialog :model-value="pending.length > 0" max-width="440" persistent>
    <v-card>
      <v-card-title class="text-h6">{{ t('account.rulesUpdated') }}</v-card-title>
      <v-card-text class="text-body-2">
        <p class="mb-3">{{ t('account.rulesUpdatedBody') }}</p>
        <ul class="pl-4">
          <li v-for="doc in pending" :key="doc.document">
            <router-link
              :to="{ name: doc.document === 'terms' ? 'LegalTerms' : 'LegalPrivacy' }"
              target="_blank"
              class="text-primary text-decoration-none"
            >
              {{ doc.title }}
            </router-link>
            <span style="color: var(--muted)">
              · {{ t('account.legalEffectiveDate', { date: doc.effectiveDate }) }}
            </span>
          </li>
        </ul>
        <p v-if="error" class="mt-3" style="color: rgb(var(--v-theme-error))">{{ error }}</p>
      </v-card-text>
      <v-card-actions class="justify-end pa-4">
        <v-btn variant="text" :disabled="accepting" @click="decline">{{ t('account.disagreeAndSignOut') }}</v-btn>
        <v-btn color="primary" variant="flat" :loading="accepting" @click="accept">
          {{ t('account.agreeAndContinue') }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
/**
 * 协议实质变更后的重新同意（#1486）。
 *
 * 挂在应用外壳上、看登录身份，而不是挂在某个登录接口的返回上：登录有密码、
 * SRP、通行密钥、第三方、令牌续签好几条路，冷打开还会直接恢复会话——只有
 * 「现在是谁登着」对所有这些都成立。协议页本身不在外壳里（router/legal.ts），
 * 所以从这里点开协议不会被这个弹窗盖住。
 *
 * 不同意就退出登录；退出后没有身份，弹窗也就不会再出现，直到下次登录。
 */
import type { LegalDocumentSummary } from '@/network/api/legal/types'

import { ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { t } from '@/i18n'
import { LegalApi } from '@/network/api/legal'
import { UserApi } from '@/network/api/users'
import { requestErrorMessage } from '@/network/utils/requestErrorMessage'
import AccountService, { currentUserId } from '@/services/account'

const router = useRouter()
const pending = ref<LegalDocumentSummary[]>([])
const accepting = ref(false)
const error = ref('')

watch(
  currentUserId,
  async (id) => {
    pending.value = []
    error.value = ''
    if (id === undefined) return
    try {
      const { data } = await LegalApi.getPendingConsents()
      // 请求途中换了人（退出、切账号）：这份答案属于上一个人。
      if (currentUserId.value === id) pending.value = data.pending
    } catch {
      // 查不到就不拦：这个弹窗挡住的是整个应用，一次失败的查询不该把人关在门外。
    }
  },
  { immediate: true }
)

async function accept() {
  accepting.value = true
  error.value = ''
  try {
    await LegalApi.acceptDocuments(Object.fromEntries(pending.value.map((d) => [d.document, d.version])))
    pending.value = []
  } catch (e) {
    error.value = requestErrorMessage(e, t('account.consentFailed'))
  } finally {
    accepting.value = false
  }
}

async function decline() {
  pending.value = []
  // 和用户菜单里的退出同一个顺序：先让服务端作废刷新令牌，否则下次打开页面
  // 会话又被恢复回来。
  try {
    await UserApi.logout()
  } catch (e) {
    console.warn('Logout request failed; clearing local session anyway:', e)
  } finally {
    await AccountService.logout()
    router.push({ name: 'SignIn' })
  }
}
</script>
