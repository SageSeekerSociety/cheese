<template>
  <div v-if="offer">
    <AccountHeading :title="t('account.passkeyOffer.title')" :lede="t('account.passkeyOffer.lede')" />

    <v-alert v-if="errorMessage" type="error" variant="tonal" density="comfortable" class="mb-6">
      {{ errorMessage }}
    </v-alert>

    <!-- Declining is a button the same size as accepting, not a small link:
         the person is choosing, not being steered. -->
    <div class="account-actions">
      <v-btn block color="primary" size="large" :loading="adding" :disabled="!!declining" @click="add">
        {{ t('account.passkeyOffer.add') }}
      </v-btn>
      <v-btn
        block
        variant="outlined"
        color="on-surface"
        size="large"
        :loading="declining === 'later'"
        :disabled="adding || declining === 'forever'"
        @click="decline(false)"
      >
        {{ t('account.passkeyOffer.later') }}
      </v-btn>
    </div>

    <div v-if="offer.canStopAsking" class="account-foot">
      <button
        type="button"
        class="account-link account-link--quiet"
        :disabled="adding || !!declining"
        @click="decline(true)"
      >
        {{ t('account.passkeyOffer.stopAsking') }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'

import { SudoCancelledError, withSudo } from '@/utils/sudo'

import { currentPasskeyOffer, endPasskeyOffer, Enrollment } from './passkeyEnrollment'
import { passkeyWrongHostMessage } from './passkeyHost'

import AccountHeading from '@/components/account/AccountHeading.vue'
import { t } from '@/i18n'
import { UserApi } from '@/network/api/users'

const router = useRouter()

// Only a sign-in that just finished leads here; opened any other way, there
// is nothing to offer.
const offer = currentPasskeyOffer()
if (!offer) router.replace('/')

const adding = ref(false)
const declining = ref<'later' | 'forever' | null>(null)
const errorMessage = ref('')
let left = false

function leave() {
  if (left) return
  left = true
  endPasskeyOffer()
  router.replace('/')
}

// Within a few minutes of signing in, the sign-in's own ticket is enough.
// Past that, the person confirms it is them first, as anywhere else.
async function register(enrollment: Enrollment) {
  if (enrollment.usable) return enrollment.register()
  await withSudo('passkey:add', (ticket) => new Enrollment(enrollment.userId, ticket).register())
}

async function add() {
  if (!offer || adding.value) return
  adding.value = true
  errorMessage.value = ''
  try {
    await register(offer.enrollment)
    toast.success(t('account.security.passkeyAddedToast'))
    leave()
  } catch (error: any) {
    // Closing the confirmation is a choice, not a failure.
    if (error instanceof SudoCancelledError) return
    // The browser's own WebAuthn error text is English and names internals.
    errorMessage.value =
      passkeyWrongHostMessage(error, offer.enrollment.rpId) ??
      (error?.name === 'NotAllowedError'
        ? t('account.security.passkeyCanceled')
        : error?.name === 'InvalidStateError'
          ? t('account.security.passkeyExists')
          : t('account.security.passkeyAddFailed'))
  } finally {
    adding.value = false
  }
}

async function decline(forever: boolean) {
  if (!offer || declining.value) return
  declining.value = forever ? 'forever' : 'later'
  try {
    await UserApi.dismissPasskeyPrompt(offer.enrollment.userId, forever)
  } catch (error) {
    // Not recorded means it is offered again next time, which is no reason
    // to keep the person here.
    console.debug('passkey: could not record the answer to the offer', error)
  }
  leave()
}

onMounted(async () => {
  if (!offer) return
  // The password manager may still add one after this screen appeared.
  if (await offer.created) {
    toast.success(t('account.security.passkeyAddedToast'))
    leave()
  }
})

onBeforeUnmount(() => {
  left = true
})
</script>
