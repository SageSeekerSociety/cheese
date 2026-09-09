<template>
  <div>
    <!-- 标题区域 -->
    <div class="mb-12">
      <div class="d-flex align-center mb-3">
        <v-icon color="primary" size="28" class="mr-3">mdi-email-check</v-icon>
        <h1 class="text-h3 font-weight-light" style="color: var(--ink); line-height: 1.2">
          {{ t('website.verifyYourEmail') }}
        </h1>
      </div>
      <p class="text-body-1" style="color: var(--muted); line-height: 1.5">
        {{ t('website.weSentAVerificationCodeTo') }}
        <strong style="color: var(--text)">{{ signupStore.email }}</strong> {{ t('website.sentenceEnd2') }}
      </p>
    </div>

    <v-fade-transition mode="out-in">
      <div :key="String(isSubmitting)">
        <!-- 验证表单区域 -->
        <div class="mb-8">
          <v-form @submit.prevent="submit">
            <v-otp-input
              v-model="otp"
              length="6"
              type="number"
              variant="outlined"
              :loading="isSubmitting"
              v-bind="otpProps"
              class="mb-6"
              @update:model-value="handleOtpInput"
            />

            <v-btn
              block
              color="primary"
              size="large"
              type="submit"
              :loading="isSubmitting"
              :disabled="otp?.length !== 6"
              style="text-transform: none; font-weight: 500; height: 48px"
              class="mb-6"
            >
              {{ t('website.finishRegistration') }}
            </v-btn>

            <div class="d-flex align-center justify-space-between">
              <p class="text-body-2" style="color: var(--muted)">
                {{ t('website.didntReceiveACode') }}
                <v-btn
                  variant="text"
                  color="primary"
                  size="small"
                  style="text-transform: none; padding: 0; min-width: auto; height: auto; vertical-align: baseline"
                  class="text-decoration-none"
                  @click="handleResend"
                >
                  {{ t('website.resendCode') }}
                </v-btn>
              </p>
              <v-btn variant="text" color="primary" to="/account/signin" size="small" style="text-transform: none">
                {{ t('website.backToSignIn') }}
              </v-btn>
            </div>
          </v-form>
        </div>
      </div>
    </v-fade-transition>
  </div>
</template>

<script lang="ts" setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'
import { toTypedSchema } from '@vee-validate/zod'
import { useForm } from 'vee-validate'
import { z } from 'zod'

import { vuetifyConfig } from '@/utils/form'

import { t } from '@/i18n'
import { useSignupStore } from '@/stores/signup'

const router = useRouter()

const signupStore = useSignupStore()

const { handleSubmit, defineField, isSubmitting } = useForm({
  validationSchema: computed(() =>
    toTypedSchema(
      z.object({
        otp: z
          .string()
          .length(6, { message: t('website.enterASixdigitVerificationCode') })
          .default(''),
      })
    )
  ),
})

const [otp, otpProps] = defineField('otp', vuetifyConfig)
const submit = handleSubmit(async ({ otp }) => {
  try {
    const username = signupStore.username
    const res = await signupStore.signup(otp)
    if (res) {
      toast.success(t('website.accountCreated'))
      router.push({
        name: 'SignIn',
        query: {
          username,
          message: t('website.yourAccountIsReadySignInTo'),
        },
      })
    }
  } catch (error) {
    toast.error(error instanceof Error ? error.message : t('website.verificationFailed'))
  }
})

const handleOtpInput = (value: string) => {
  if (value.length === 6) {
    submit()
  }
}

const handleResend = async () => {
  try {
    // await signupStore.resendVerification()
    toast.success(t('website.verificationCodeResent'))
  } catch (error) {
    toast.error(error instanceof Error ? error.message : t('website.couldNotSendTheCode'))
  }
}
</script>
