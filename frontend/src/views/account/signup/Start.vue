<template>
  <div>
    <!-- 邮箱域名提醒弹窗 -->
    <v-dialog v-model="showDomainWarning" max-width="440">
      <v-card>
        <v-card-item
          prepend-icon="mdi-email-alert"
          :title="t('account.chooseAnEmailAddress')"
          class="bg-info-container"
        />
        <v-card-text class="pt-4">
          <p class="text-body-1 mb-3">
            {{ t('account.weRecommendA') }} <strong>{{ t('account.workEmail') }}</strong> {{ t('account.or') }}
            <strong>{{ t('account.universityEmail') }}</strong>
            {{ t('account.forRegistration') }}
          </p>
          <p class="text-body-2 text-medium-emphasis">{{ t('account.someChallengesAreOnlyAvailableToParticular') }}</p>
        </v-card-text>
        <v-card-actions class="justify-end pa-4">
          <v-btn variant="text" @click="showDomainWarning = false">{{ t('account.close') }}</v-btn>
          <v-btn color="primary" variant="flat" @click="onDomainWarningConfirm">{{ t('account.dontShowAgain') }}</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 标题区域 - 美观大气 -->
    <div class="mb-12">
      <div class="d-flex align-center mb-3">
        <v-icon color="primary" size="28" class="mr-3">mdi-account-plus</v-icon>
        <h1 class="text-h3 font-weight-light" style="color: var(--ink); line-height: 1.2">
          {{ t('account.joinCheese') }}
        </h1>
      </div>
      <p class="text-body-1" style="color: var(--muted); line-height: 1.5">
        {{ t('account.startSharingKnowledgeWithYourTeam') }}
      </p>
    </div>

    <!-- 错误提示区域 -->
    <div v-if="error" class="mb-8">
      <v-alert closable type="error" variant="tonal" density="comfortable">
        {{ error }}
      </v-alert>
    </div>

    <v-fade-transition>
      <!-- 注册表单区域 -->
      <div class="mb-8">
        <v-form ref="signupForm" @submit.prevent="submit">
          <v-row dense>
            <v-col cols="12" md="6">
              <v-text-field
                id="signup-username"
                v-model="username"
                name="username"
                autocomplete="username"
                :label="t('account.username')"
                variant="outlined"
                :loading="isSubmitting"
                v-bind="usernameProps"
                class="mb-4"
              />
            </v-col>
            <v-col cols="12" md="6">
              <v-text-field
                id="signup-nickname"
                v-model="nickname"
                name="nickname"
                autocomplete="nickname"
                :label="t('account.displayName')"
                variant="outlined"
                :loading="isSubmitting"
                v-bind="nicknameProps"
                class="mb-4"
              />
            </v-col>
          </v-row>

          <v-row dense>
            <v-col cols="12" md="6">
              <v-text-field
                id="signup-password"
                v-model="password"
                name="password"
                autocomplete="new-password"
                :label="t('account.password')"
                type="password"
                variant="outlined"
                :loading="isSubmitting"
                v-bind="passwordProps"
                class="mb-4"
              />
            </v-col>
            <v-col cols="12" md="6">
              <v-text-field
                id="signup-confirm-password"
                v-model="confirmPassword"
                name="confirmPassword"
                autocomplete="new-password"
                :label="t('account.confirmPassword')"
                type="password"
                variant="outlined"
                :loading="isSubmitting"
                v-bind="confirmPasswordProps"
                class="mb-4"
              />
            </v-col>
          </v-row>

          <v-text-field
            id="signup-email"
            v-model="email"
            name="email"
            autocomplete="email"
            :label="t('account.emailAddress')"
            type="email"
            variant="outlined"
            :loading="isSubmitting"
            v-bind="emailProps"
            class="mb-6"
          />

          <v-text-field
            v-if="requireInviteCode"
            v-model="inviteCode"
            autocomplete="off"
            :label="t('account.invitationCode')"
            variant="outlined"
            :loading="isSubmitting"
            v-bind="inviteCodeProps"
            class="mb-6"
          />

          <div class="d-flex justify-space-between align-center mb-6">
            <v-checkbox v-model="agree" density="compact" v-bind="agreeProps" hide-details>
              <template #label>
                <span class="text-body-2" style="color: var(--muted); line-height: 1.4">
                  {{ t('account.iAgreeToThe') }}
                  <a href="#" class="text-primary text-decoration-none">{{ t('account.termsOfService') }}</a>
                  {{ t('account.and') }}
                  <a href="#" class="text-primary text-decoration-none">{{ t('account.privacyPolicy') }}</a>
                </span>
              </template>
            </v-checkbox>
            <v-btn variant="text" color="primary" to="/account/signin" size="small" style="text-transform: none">
              {{ t('account.iHaveAnAccount') }}
            </v-btn>
          </div>

          <v-btn
            block
            color="primary"
            size="large"
            type="submit"
            :loading="isSubmitting"
            :disabled="!registrationConfigReady"
            style="text-transform: none; font-weight: 500; height: 48px"
            class="mb-4"
          >
            {{ t('account.createAccount') }}
          </v-btn>

          <p class="text-body-2" style="color: var(--muted)">
            {{ t('account.alreadyHaveAnAccount') }}
            <v-btn
              variant="text"
              color="primary"
              to="/account/signin"
              size="small"
              style="text-transform: none; padding: 0; min-width: auto; height: auto; vertical-align: baseline"
              class="text-decoration-none"
            >
              {{ t('account.signIn2') }}
            </v-btn>
          </p>
        </v-form>
      </div>
    </v-fade-transition>
  </div>
</template>

<script lang="ts" setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { toTypedSchema } from '@vee-validate/zod'
import * as srp from 'secure-remote-password/client'
import { useForm } from 'vee-validate'
import { z } from 'zod'

import { REGEX_PASSWORD, vuetifyConfig } from '@/utils/form'

import { t } from '@/i18n'
import { UserApi } from '@/network/api/users'
import { requestErrorMessage } from '@/network/utils/requestErrorMessage'
import { useSignupStore } from '@/stores/signup'

const error = ref('')
const requireInviteCode = ref(false)
const registrationConfigReady = ref(false)

const { handleSubmit, defineField, isSubmitting } = useForm({
  validationSchema: computed(() =>
    toTypedSchema(
      z
        .object({
          username: z
            .string()
            .min(4)
            .max(32)
            .regex(/^[a-zA-Z0-9_-]{4,32}$/, {
              message: t('account.useLettersNumbersUnderscoresOrHyphensFor'),
            }),
          nickname: z
            .string()
            .min(1)
            .max(16)
            .regex(/^[a-zA-Z0-9_\u4e00-\u9fa5]{1,16}$/, {
              message: t('account.useLettersNumbersUnderscoresOrChineseCharacters'),
            }),

          password: z
            .string()
            .min(8)
            .regex(REGEX_PASSWORD, {
              message: t('account.yourPasswordMustContainALetterA'),
            }),

          confirmPassword: z
            .string()
            .min(8)
            .regex(REGEX_PASSWORD, {
              message: t('account.yourPasswordMustContainALetterA'),
            }),
          email: z.string().email(),
          inviteCode: z.string().optional(),
          agree: z.boolean().refine((v) => v, { message: t('account.pleaseAcceptTheTermsOfServiceAnd') }),
        })
        .superRefine(({ password, confirmPassword, inviteCode }, ctx) => {
          if (password !== confirmPassword) {
            ctx.addIssue({
              code: z.ZodIssueCode.custom,
              path: ['confirmPassword'],
              message: t('account.passwordsDoNotMatch'),
            })
          }
          if (requireInviteCode.value && !inviteCode?.trim()) {
            ctx.addIssue({
              code: z.ZodIssueCode.custom,
              path: ['inviteCode'],
              message: t('account.enterAnInvitationCode'),
            })
          }
        })
    )
  ),
})

const [username, usernameProps] = defineField('username', vuetifyConfig)
const [nickname, nicknameProps] = defineField('nickname', vuetifyConfig)
const [password, passwordProps] = defineField('password', vuetifyConfig)
const [confirmPassword, confirmPasswordProps] = defineField('confirmPassword', vuetifyConfig)
const [email, emailProps] = defineField('email', vuetifyConfig)
const [inviteCode, inviteCodeProps] = defineField('inviteCode', vuetifyConfig)
const [agree, agreeProps] = defineField('agree', vuetifyConfig)

const signupStore = useSignupStore()
const router = useRouter()

const DOMAIN_WARNING_KEY = 'cheese:domain_warning_seen'
const showDomainWarning = ref(false)

function onDomainWarningConfirm() {
  try {
    localStorage.setItem(DOMAIN_WARNING_KEY, '1')
  } catch {
    // localStorage unavailable
  }
  showDomainWarning.value = false
}

onMounted(async () => {
  try {
    if (localStorage.getItem(DOMAIN_WARNING_KEY) !== '1') {
      showDomainWarning.value = true
    }
  } catch {
    // localStorage unavailable, skip
  }

  try {
    const { data } = await UserApi.getRegistrationConfig()
    requireInviteCode.value = data.requireInviteCode
    registrationConfigReady.value = true
  } catch (e) {
    error.value = requestErrorMessage(e, t('account.registrationSettingsCouldNotBeLoadedRefresh'))
  }
})

const submit = handleSubmit(async (value) => {
  try {
    // 生成 SRP 盐值和验证器
    const srpSalt = srp.generateSalt()
    const privateKey = srp.derivePrivateKey(srpSalt, value.username, value.password)
    const srpVerifier = srp.deriveVerifier(privateKey)

    // 将 SRP 参数保存到 store 中，供后续注册使用
    await signupStore.startSignup({
      ...value,
      inviteCode: requireInviteCode.value ? value.inviteCode?.trim() : undefined,
      srpSalt,
      srpVerifier,
    })

    router.push('/account/signup/verify-email')
  } catch (e) {
    error.value = requestErrorMessage(e, t('account.registrationFailedPleaseTryAgain'))
  }
})
</script>
