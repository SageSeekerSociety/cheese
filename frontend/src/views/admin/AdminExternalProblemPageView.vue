<script setup lang="ts">
import { useI18n } from 'vue-i18n'

import AdminPage from '@/components/admin/AdminPage.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import SaveStatus from '@/components/base/SaveStatus.vue'

// 管理后台的「外站题账号」（`/admin/integrations/external-problem`）的画法：状态行、
// 两格凭据、给管理员的说明、保存回执。取数与写都在页面里
// （`AdminExternalProblemPage.vue`），这里只吃数据、往上报动作。
//
// 两件事决定了这一块的形状（判据与来由见页面文件开头）：
// 1. 密码只写不回显 —— 输入框绑一支模型、永远从空开始；空着提交表示「不改已经存下的
//    那一个」，所以这一格下面挂一句 hint 把这件事说给填的人。
// 2. 读失败时整块换成「读不到」加重试 —— 这时候我们并没有「配没配过」那个答案，所以不画
//    状态行也不画表单。判据是 `!== null` 而不是真值：原话取不到时是空串，仍要给出错态。
defineOptions({ name: 'AdminExternalProblemPageView' })

const { t } = useI18n()

defineProps<{
  loading: boolean
  /** 读这一页失败时是**服务端原话**（原话取不到就空串）；`null` 表示没失败。 */
  loadError: string | null
  /** 读回来之后才敢画的那行答案：有没有配过、配的是哪个账号。 */
  status: string
  /** 首屏还没读回来时不画状态行：这时候还没有答案。 */
  showStatus: boolean
  /** 状态行那个 `data-configured`：有没有配过。 */
  configured: boolean
  updated: string
  saving: boolean
  saved: boolean
  /** 保存失败。就地回执，表单里的东西一个字不动。 */
  saveError: string
  /** 表单没填全（账号为空）—— 这是填写本身的问题，还没发请求。 */
  validationError: string
}>()

const emit = defineEmits<{
  retry: []
  save: []
}>()

const username = defineModel<string>('username', { required: true })
const password = defineModel<string>('password', { required: true })
</script>

<template>
  <AdminPage :title="t('navigation.admin.externalProblem')" :sub="t('integrations.admin.externalProblem.sub')">
    <div class="aep__body admin-form-card">
      <!-- 读失败：标题说清是哪一页没读到，服务端原话作说明行，重试就在旁边。
           **不**接着画「还没配置」和那张表单 —— 见文件开头第 2 条。
           判据是 `!== null` 而不是真值：原话取不到时 `loadError` 是空串，仍要给出错态。 -->
      <BaseLoadError
        v-if="loadError !== null"
        :title="t('integrations.admin.externalProblem.loadFailed')"
        :error="loadError || undefined"
        :retry-label="t('integrations.admin.externalProblem.retry')"
        @retry="emit('retry')"
      />

      <template v-else>
        <p v-if="showStatus" class="aep__status t-body" :data-configured="configured ? 'yes' : 'no'">
          {{ status }}
        </p>
        <p v-if="updated" class="aep__meta t-meta">{{ updated }}</p>

        <!-- 这句是给填的人看的实话：这个账号会被平台用来读 YOJ 上的成绩，所以该填校方
             给的那一个；也把「平台会拿它做什么」说清楚，免得填的人自己猜。 -->
        <p class="aep__note t-body">{{ t('integrations.admin.externalProblem.note') }}</p>

        <div class="aep__form">
          <v-text-field
            v-model="username"
            autocomplete="off"
            :label="t('integrations.admin.externalProblem.username')"
            :disabled="loading"
          />
          <v-text-field
            v-model="password"
            class="aep__password"
            autocomplete="new-password"
            type="password"
            :label="t('integrations.admin.externalProblem.password')"
            :hint="t('integrations.admin.externalProblem.passwordHint')"
            persistent-hint
          />
          <p v-if="validationError" role="alert" class="aep__error t-body">{{ validationError }}</p>
          <div class="aep__actions">
            <SaveStatus
              :saving="saving"
              :saved="saved"
              :error="saveError"
              :saved-text="t('integrations.admin.externalProblem.saved')"
            />
            <BaseButton kind="primary" :loading="saving" :disabled="loading" @click="emit('save')">
              {{ t('integrations.admin.externalProblem.save') }}
            </BaseButton>
          </div>
        </div>
      </template>
    </div>
  </AdminPage>
</template>

<style scoped>
/* 卡片的宽度（720 上限）、内边距（20/24）和内缩（16/24）都由 `.admin-form-card` 给，
   和别的表单页同一套。 */

.aep__status {
  margin: 0;
  color: var(--ink);
}

.aep__meta {
  margin: 4px 0 0;
  color: var(--faint);
}

.aep__note {
  margin: 8px 0 0;
  color: var(--ink);
}

.aep__form {
  margin-top: 16px;
}

/* 密码那格下面挂着 hint，而下一个动作行的按钮有一半悬在它自己框的上沿之上
   （见 `.claude/rules/frontend.md`），中间没有余量时两行字会叠在一起。给这一格
   留一段底距，按钮就落在空处。 */
.aep__password {
  margin-bottom: 12px;
}

.aep__error {
  margin: 8px 0 0;
  color: var(--danger-ink);
}

.aep__actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 8px;
}

/* 窄屏的两侧收窄由 `.admin-form-card` 自己那条 ≤700 规则给，这一页不再各写一份。 */
</style>
