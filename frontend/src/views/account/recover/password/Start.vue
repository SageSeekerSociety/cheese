<template>
  <div>
    <!-- 标题区域 - 美观大气 -->
    <div class="mb-12">
      <div class="d-flex align-center mb-3">
        <v-icon color="primary" size="28" class="mr-3">mdi-lock-reset</v-icon>
        <h1 class="text-h3 font-weight-light" style="color: var(--ink); line-height: 1.2">
          {{ t('account.resetYourPassword') }}
        </h1>
      </div>
      <p class="text-body-1" style="color: var(--muted); line-height: 1.5">
        {{ t('account.verifyYourIdentityWithYourAccountEmail') }}
      </p>
    </div>

    <!-- 错误/成功提示区域 -->
    <div v-if="myAlert.message" class="mb-8">
      <v-alert :type="myAlert.type" variant="tonal" density="comfortable">
        {{ myAlert.message }}
      </v-alert>
    </div>

    <v-fade-transition mode="out-in">
      <div :key="String(isSubmitting)">
        <!-- 重置表单区域 -->
        <div class="mb-8">
          <v-form @submit.prevent="submit">
            <v-text-field
              id="field-email"
              v-model="email"
              autocomplete="email"
              name="email"
              :label="t('account.accountEmail')"
              variant="outlined"
              :loading="isSubmitting"
              v-bind="emailProps"
              class="mb-6"
            />

            <v-btn
              block
              color="primary"
              size="large"
              type="submit"
              :loading="isSubmitting"
              style="text-transform: none; font-weight: 500; height: 48px"
              class="mb-4"
            >
              {{ t('account.sendResetEmail') }}
            </v-btn>

            <p class="text-body-2" style="color: var(--muted)">
              {{ t('account.rememberYourPassword') }}
              <v-btn
                variant="text"
                color="primary"
                to="/account/signin"
                size="small"
                style="text-transform: none; padding: 0; min-width: auto; height: auto; vertical-align: baseline"
                class="text-decoration-none"
              >
                <v-icon start size="16">mdi-arrow-left</v-icon> {{ t('account.backToSignIn') }}
              </v-btn>
            </p>
          </v-form>
        </div>
      </div>
    </v-fade-transition>
  </div>
</template>

<script lang="ts" setup>
import { computed, ref } from 'vue'
// import { useRouter } from 'vue-router'
import { toTypedSchema } from '@vee-validate/zod'
import { useForm } from 'vee-validate'
import { z } from 'zod'

import { vuetifyConfig } from '@/utils/form'

import { t } from '@/i18n'
import { UserApi } from '@/network/api/users'
import { requestErrorMessage } from '@/network/utils/requestErrorMessage'

const myAlert = ref<{
  message: string | undefined
  type: 'success' | 'info' | 'warning' | 'error' | undefined
}>({
  message: '',
  type: 'error',
})

const { handleSubmit, defineField, isSubmitting } = useForm({
  validationSchema: computed(() =>
    toTypedSchema(
      z.object({
        email: z.string().email(),
      })
    )
  ),
})

const [email, emailProps] = defineField('email', vuetifyConfig)

// const router = useRouter()

const submit = handleSubmit(async (value) => {
  try {
    await UserApi.recoverPasswordRequest(value.email)
    myAlert.value = {
      message: t('account.checkYourInboxForAPasswordReset'),
      type: 'success',
    }
  } catch (e) {
    myAlert.value = {
      message: requestErrorMessage(e, t('account.couldNotSendTheEmailPleaseTry')),
      type: 'error',
    }
  }
})
</script>
@/network/api/users/user
