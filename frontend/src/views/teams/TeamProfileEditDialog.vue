<script setup lang="ts">
// 小队资料编辑弹窗：侧栏名字旁那支铅笔点开的东西。只管「别人看到的小队」这一层
// —— 名字、介绍、头像。地址（handle）、可见性、加入方式不在这里：它们在成员页的
// 「团队地址与加入」卡上，而且对个人小队根本不成立（后端直接报错），混进来只会
// 多出一组要藏的分支。
//
// 填表的人自己拿着草稿：填到一半的名字、介绍、挑好的图只有它知道。校验过（名字不能
// 空是自己判的）就把整份草稿报上去（`save`），之后的传图、PATCH、报错怎么落到哪一格
// 都在外面 —— 所以 `saving`、`error`、`nameError` 都是 props 进来的。
import type { Team } from '@/types'

import { computed, ref, watch } from 'vue'

import { getAvatarUrl } from '@/utils/materials'

import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import AvatarUploader from '@/components/common/AvatarUploader.vue'
import { t } from '@/i18n'

/** 报上去的那份草稿。头像没换就不带这一项，免得把现成的头像覆盖掉。 */
interface TeamProfileDraft {
  name: string
  intro: string
  avatarFile?: File
}

const props = defineProps<{
  modelValue: boolean
  team: Team
  /** 正在保存（传图 + PATCH 都算）：按钮转起来，也挡住第二次提交。 */
  saving?: boolean
  /** 上一次为什么没存上：没权限、没网。 */
  error?: string
  /** 上一次为什么名字不认：撞名了。落在名字那一格。 */
  nameError?: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  save: [draft: TeamProfileDraft]
}>()

const name = ref('')
const intro = ref('')
const avatarFile = ref<File>()
const requiredError = ref('')
// 上一次报上去的名字。外面说「这个名字被占了」时，那句话说的是这个名字 —— 人一动
// 名字，这句话就过期了，不用等外面再吩咐一次。
const submittedName = ref<string | null>(null)
const nameComplaint = computed(() =>
  props.nameError && name.value.trim() === submittedName.value ? props.nameError : ''
)

// 每次打开都从小队现在的样子起手：上一次取消留下的半截输入不该跟到下一次，
// 上一次的报错也一样。`immediate` 是为了「打开着被挂上去」也算一次起手。
watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    name.value = props.team.name
    intro.value = props.team.intro ?? ''
    avatarFile.value = undefined
    requiredError.value = ''
    submittedName.value = null
  },
  { immediate: true }
)

function save() {
  requiredError.value = ''
  const trimmedName = name.value.trim()
  if (!trimmedName) {
    requiredError.value = t('work.teamProfile.nameRequired')
    return
  }
  if (props.saving) return
  submittedName.value = trimmedName
  emit('save', {
    name: trimmedName,
    intro: intro.value.trim(),
    ...(avatarFile.value ? { avatarFile: avatarFile.value } : {}),
  })
}
</script>

<template>
  <AdaptiveDialog
    :model-value="modelValue"
    :title="t('work.teamProfile.editTitle')"
    :primary-label="t('work.teamProfile.save')"
    :primary-loading="saving"
    :close-disabled="saving"
    @update:model-value="emit('update:modelValue', $event)"
    @primary="save"
  >
    <v-alert v-if="error" type="error" class="mb-4">{{ error }}</v-alert>
    <v-container fluid>
      <v-row>
        <v-col cols="12" md="4" class="text-center">
          <avatar-uploader v-model="avatarFile" :src="getAvatarUrl(team.avatarId)" />
          <p class="text-body-2 text-medium-emphasis mb-2">{{ t('work.teamProfile.avatarLabel') }}</p>
        </v-col>
        <v-col cols="12" md="8">
          <v-text-field
            v-model="name"
            autocomplete="off"
            :label="t('work.teamProfile.nameLabel')"
            variant="outlined"
            color="primary"
            :error-messages="requiredError || nameComplaint"
            class="mb-4"
            rounded="md"
            @update:model-value="requiredError = ''"
          ></v-text-field>

          <v-textarea
            v-model="intro"
            autocomplete="off"
            :label="t('work.teamProfile.introLabel')"
            :hint="t('work.teamProfile.introHint')"
            counter="250"
            maxlength="250"
            rows="3"
            auto-grow
            persistent-hint
            variant="outlined"
            color="primary"
            rounded="md"
          ></v-textarea>
        </v-col>
      </v-row>
    </v-container>
  </AdaptiveDialog>
</template>
