<!--
  In the desktop app, the end of a sign-in made in the browser (BackToApp.vue):
  the code from the link and the secret this app kept (lib/desktopApp.ts) are
  traded for a sign-in of its own.
-->
<template>
  <div>
    <template v-if="failed">
      <AccountHeading :title="t('account.oauth.error.title')" />
      <v-alert type="error" variant="tonal" density="comfortable" class="mb-6">
        {{ t('account.oauth.app.expired') }}
      </v-alert>
      <BaseButton block kind="primary" size="lg" to="/account/signin" class="account-submit">
        {{ t('account.backToSignIn') }}
      </BaseButton>
    </template>

    <template v-else>
      <AccountHeading :title="t('account.oauth.success.processing')" />
      <v-progress-linear indeterminate color="primary" height="2" />
    </template>
  </div>
</template>

<script lang="ts" setup>
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import AccountHeading from '@/components/account/AccountHeading.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'
import { takeSignInVerifier } from '@/lib/desktopApp'
import { UserApi } from '@/network/api/users'
import { postLoginTarget } from '@/router/loginRedirect'
import AccountService from '@/services/account'

const route = useRoute()
const router = useRouter()

const failed = ref(false)

onMounted(async () => {
  const code = route.query.code
  // Only a sign-in this app started, and only once.
  const kept = takeSignInVerifier()
  if (typeof code !== 'string' || !kept) {
    failed.value = true
    return
  }
  try {
    await UserApi.finishAppSignIn(code, kept.verifier)
  } catch {
    failed.value = true
    return
  }
  if ((await AccountService.resumeFromCookie()) !== 'ok') {
    failed.value = true
    return
  }
  router.replace(postLoginTarget({ redirect: kept.target }))
})
</script>
