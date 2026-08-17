<template>
  <div>
    <!-- 邮箱域名提醒弹窗 -->
    <v-dialog v-model="showDomainWarning" max-width="440">
      <v-card>
        <v-card-item prepend-icon="mdi-email-alert" title="邮箱建议" class="bg-info-container" />
        <v-card-text class="pt-4">
          <p class="text-body-1 mb-3">建议使用<strong>企业邮箱</strong>或<strong>学校邮箱</strong>注册。</p>
          <p class="text-body-2 text-medium-emphasis">
            部分赛题可能仅对特定域名邮箱开放，使用个人邮箱可能影响您查看或参与这些内容。
          </p>
        </v-card-text>
        <v-card-actions class="justify-end pa-4">
          <v-btn variant="text" @click="showDomainWarning = false">关闭</v-btn>
          <v-btn color="primary" variant="flat" @click="onDomainWarningConfirm">不再提醒</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 标题区域 - 美观大气 -->
    <div class="mb-12">
      <div class="d-flex align-center mb-3">
        <v-icon color="primary" size="28" class="mr-3">mdi-account-plus</v-icon>
        <h1 class="text-h3 font-weight-light" style="color: var(--ink); line-height: 1.2">加入知是社区</h1>
      </div>
      <p class="text-body-1" style="color: var(--muted); line-height: 1.5">开启您的知识共享之旅</p>
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
                v-model="username"
                label="用户名"
                variant="outlined"
                :loading="isSubmitting"
                v-bind="usernameProps"
                class="mb-4"
              />
            </v-col>
            <v-col cols="12" md="6">
              <v-text-field
                v-model="nickname"
                label="显示名称"
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
                v-model="password"
                label="密码"
                type="password"
                variant="outlined"
                :loading="isSubmitting"
                v-bind="passwordProps"
                class="mb-4"
              />
            </v-col>
            <v-col cols="12" md="6">
              <v-text-field
                v-model="confirmPassword"
                label="确认密码"
                type="password"
                variant="outlined"
                :loading="isSubmitting"
                v-bind="confirmPasswordProps"
                class="mb-4"
              />
            </v-col>
          </v-row>

          <v-text-field
            v-model="email"
            label="电子邮箱"
            type="email"
            variant="outlined"
            :loading="isSubmitting"
            v-bind="emailProps"
            class="mb-6"
          />

          <v-text-field
            v-if="requireInviteCode"
            v-model="inviteCode"
            label="邀请码"
            variant="outlined"
            :loading="isSubmitting"
            v-bind="inviteCodeProps"
            class="mb-6"
          />

          <div class="d-flex justify-space-between align-center mb-6">
            <v-checkbox v-model="agree" density="compact" v-bind="agreeProps" hide-details>
              <template #label>
                <span class="text-body-2" style="color: var(--muted); line-height: 1.4">
                  同意 <a href="#" class="text-primary text-decoration-none">用户协议</a>和
                  <a href="#" class="text-primary text-decoration-none">隐私政策</a>
                </span>
              </template>
            </v-checkbox>
            <v-btn variant="text" color="primary" to="/account/signin" size="small" style="text-transform: none">
              已有账号
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
            立即注册
          </v-btn>

          <p class="text-body-2" style="color: var(--muted)">
            已经有账号了？
            <v-btn
              variant="text"
              color="primary"
              to="/account/signin"
              size="small"
              style="text-transform: none; padding: 0; min-width: auto; height: auto; vertical-align: baseline"
              class="text-decoration-none"
            >
              立即登录
            </v-btn>
          </p>
        </v-form>
      </div>
    </v-fade-transition>
  </div>
</template>

<script lang="ts" setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { toTypedSchema } from '@vee-validate/zod'
import * as srp from 'secure-remote-password/client'
import { useForm } from 'vee-validate'
import { z } from 'zod'

import { REGEX_PASSWORD, vuetifyConfig } from '@/utils/form'

import { UserApi } from '@/network/api/users'
import { requestErrorMessage } from '@/network/utils/requestErrorMessage'
import { useSignupStore } from '@/stores/signup'

const error = ref('')
const requireInviteCode = ref(false)
const registrationConfigReady = ref(false)

const { handleSubmit, defineField, isSubmitting } = useForm({
  validationSchema: toTypedSchema(
    z
      .object({
        username: z
          .string()
          .min(4)
          .max(32)
          .regex(/^[a-zA-Z0-9_-]{4,32}$/, {
            message: '用户名只能使用英文字母、数字、下划线',
          }),
        nickname: z
          .string()
          .min(1)
          .max(16)
          .regex(/^[a-zA-Z0-9_\u4e00-\u9fa5]{1,16}$/, {
            message: '昵称只能使用英文字母、数字、下划线、中文',
          }),

        password: z.string().min(8).regex(REGEX_PASSWORD, {
          message: '密码必须包含字母、数字、特殊字符',
        }),

        confirmPassword: z.string().min(8).regex(REGEX_PASSWORD, {
          message: '密码必须包含字母、数字、特殊字符',
        }),
        email: z.string().email(),
        inviteCode: z.string().optional(),
        agree: z.boolean().refine((v) => v, { message: '请同意用户协议和隐私政策' }),
      })
      .superRefine(({ password, confirmPassword, inviteCode }, ctx) => {
        if (password !== confirmPassword) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            path: ['confirmPassword'],
            message: '两次输入的密码不一致',
          })
        }
        if (requireInviteCode.value && !inviteCode?.trim()) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            path: ['inviteCode'],
            message: '请输入邀请码',
          })
        }
      })
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
    error.value = requestErrorMessage(e, '暂时无法获取注册配置，请刷新页面重试')
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
    error.value = requestErrorMessage(e, '注册失败，请重试')
  }
})
</script>
