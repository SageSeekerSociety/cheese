<template>
  <div>
    <v-banner v-if="hasSavedInfo" class="mb-4" lines="one">
      <template #text>
        <div class="d-flex align-center justify-space-between">
          <span>{{ t('tasks.verifyForm.savedFound') }}</span>
        </div>
      </template>
      <template #actions>
        <BaseButton kind="ghost" @click="hasSavedInfo = false">{{ t('tasks.verifyForm.ignore') }}</BaseButton>
        <BaseButton kind="primary" append-icon="mdi-lightning-bolt" @click="fillSavedInfo">{{
          t('tasks.verifyForm.fill')
        }}</BaseButton>
      </template>
    </v-banner>

    <v-form ref="verifyForm" @submit.prevent="submitForm">
      <div class="contact-section">
        <div class="d-flex align-center mb-2">
          <span class="text-subtitle-2">{{ t('tasks.verifyForm.contact') }}</span>
          <v-chip class="ms-2" size="small" color="primary" variant="tonal">{{
            t('tasks.verifyForm.atLeastOne')
          }}</v-chip>
          <v-tooltip v-if="requireRealName" location="top" max-width="300">
            <template #activator="{ props }">
              <v-icon v-bind="props" color="info" size="18" class="ms-2">mdi-information-outline</v-icon>
            </template>
            <span>{{ t('tasks.verifyForm.realNameTip') }}</span>
          </v-tooltip>
        </div>

        <v-row dense>
          <v-col cols="12" sm="6">
            <v-text-field
              v-model="formData.phone"
              autocomplete="tel"
              name="phone"
              :label="t('tasks.verifyForm.phone')"
              :hint="!formData.email ? t('tasks.verifyForm.phoneOrEmail') : undefined"
              :required="!formData.email"
              prepend-inner-icon="mdi-phone"
              v-bind="phoneProps"
            ></v-text-field>
          </v-col>
          <v-col cols="12" sm="6">
            <v-text-field
              v-model="formData.email"
              autocomplete="email"
              name="email"
              :label="t('tasks.verifyForm.email')"
              :hint="!formData.phone ? t('tasks.verifyForm.phoneOrEmail') : undefined"
              :required="!formData.phone"
              prepend-inner-icon="mdi-email"
              v-bind="emailProps"
            ></v-text-field>
          </v-col>
        </v-row>
      </div>

      <v-textarea
        v-model="formData.applyReason"
        autocomplete="off"
        :label="t('tasks.verifyForm.applyReason')"
        v-bind="applyReasonProps"
        :hint="t('tasks.verifyForm.applyReasonHint')"
        rows="3"
        auto-grow
      ></v-textarea>

      <div class="d-flex align-center">
        <v-checkbox
          v-model="saveToLocal"
          :label="t('tasks.verifyForm.saveLocal')"
          color="primary"
          hide-details
          density="compact"
        >
        </v-checkbox>
        <v-tooltip location="right">
          <template #activator="{ props }">
            <v-icon v-bind="props" color="primary" size="18" class="ms-2"> mdi-shield-check </v-icon>
          </template>
          <div class="text-body-2">
            {{ t('tasks.verifyForm.saveLocalTip1') }}<br />
            {{ t('tasks.verifyForm.saveLocalTip2') }}<br />
            {{ t('tasks.verifyForm.saveLocalTip3') }}
          </div>
        </v-tooltip>
      </div>
    </v-form>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { toTypedSchema } from '@vee-validate/zod'
import { useForm } from 'vee-validate'
import { z } from 'zod'

import { vuetifyConfig } from '@/utils/form'

import BaseButton from '@/components/base/BaseButton.vue'
import { useEvents } from '@/views/tasks/events'

const { t } = useI18n()

defineProps<{
  requireRealName: boolean
}>()

const STORAGE_KEY = 'verify_info_form_data'

type VerifyInfoFormData = {
  phone?: string
  email?: string
  applyReason?: string
}

const emit = defineEmits<{
  (e: 'submit', formData: VerifyInfoFormData): void
}>()

const phoneRegex = /^1[3-9]\d{9}$/
const emailRegex = /^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/

const { handleSubmit, defineField, isSubmitting, setFieldValue } = useForm({
  validationSchema: toTypedSchema(
    z
      .object({
        phone: z.string().regex(phoneRegex, t('tasks.verifyForm.phoneInvalid')).optional().or(z.literal('')),
        email: z.string().regex(emailRegex, t('tasks.verifyForm.emailInvalid')).optional().or(z.literal('')),
        applyReason: z.string().max(500, t('tasks.verifyForm.reasonTooLong')).optional().or(z.literal('')),
      })
      .refine((data) => data.phone || data.email, {
        message: t('tasks.verifyForm.phoneOrEmail'),
        path: ['phone'], // 将错误消息显示在手机号字段
      })
  ),
})

// 修改 defineField 的配置
const fieldConfig = (state: { errors: any }) => ({
  ...vuetifyConfig(state),
  validateOnInput: false, // 输入时不验证
  validateOnChange: false, // 值变化时不验证
  validateOnBlur: true, // 失去焦点时验证
  validateOnModelUpdate: false, // 数据更新时不验证
})

const [phone, phoneProps] = defineField('phone', fieldConfig)
const [email, emailProps] = defineField('email', fieldConfig)
const [applyReason, applyReasonProps] = defineField('applyReason', fieldConfig)

const formData = reactive({
  phone,
  email,
  applyReason,
})

const verifyForm = ref<HTMLFormElement | null>(null)
const saveToLocal = ref(false)
const hasSavedInfo = ref(false)

// 使用事件总线
const events = useEvents()

// 检查本地存储
onMounted(() => {
  const savedData = localStorage.getItem(STORAGE_KEY)
  if (savedData) {
    hasSavedInfo.value = true
    // 如果有保存的信息，默认勾选保存选项
    saveToLocal.value = true
  }
})

// 填入保存的信息
const fillSavedInfo = () => {
  const savedData = localStorage.getItem(STORAGE_KEY)
  if (savedData) {
    const data = JSON.parse(savedData) as Partial<VerifyInfoFormData>
    Object.entries(data).forEach(([key, value]) => {
      if (key !== 'applyReason' && value) {
        setFieldValue(key as keyof VerifyInfoFormData, value)
      }
    })
    hasSavedInfo.value = false
  }
}

const submitForm = handleSubmit((values) => {
  if (saveToLocal.value) {
    // 保存除了申请理由之外的信息
    const dataToSave = { ...values }
    delete dataToSave.applyReason
    localStorage.setItem(STORAGE_KEY, JSON.stringify(dataToSave))
  }

  // 通过事件总线发送数据
  events.emit('verify-form-submit', values as any)
  // 通过props发送数据
  emit('submit', values as VerifyInfoFormData)
})

// 暴露方法供父组件调用
defineExpose({
  /**
   * 提交表单的方法
   */
  submit: () => {
    console.log('提交表单', verifyForm.value)
    if (verifyForm.value) {
      verifyForm.value.requestSubmit()
    } else {
      submitForm()
    }
  },
  isSubmitting,
})
</script>

<style scoped>
.contact-section {
  margin-bottom: 16px;
}
</style>
