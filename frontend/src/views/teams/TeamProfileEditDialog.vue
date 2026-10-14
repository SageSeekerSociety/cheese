<script setup lang="ts">
// 小队资料编辑弹窗：侧栏名字旁那支铅笔点开的东西。只管「别人看到的小队」这一层
// —— 名字、介绍、头像。地址（handle）、可见性、加入方式不在这里：它们在成员页的
// 「团队地址与加入」卡上，而且对个人小队根本不成立（后端直接报错），混进来只会
// 多出一组要藏的分支。
//
// 保存本身在外面做（`composables/useTeamNav.ts`）——换头像、PATCH、撞名和没权限那两种
// 拒绝怎么落格，都在那一层。这一件只收三样输入、报上去（A 档）：`busy`、两句报错都由
// 外面给。
import type { Team } from '@/types'

import { ref, watch } from 'vue'

import { getAvatarUrl } from '@/utils/materials'

import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import AvatarUploader from '@/components/common/AvatarUploader.vue'
import { t } from '@/i18n'

const props = defineProps<{
  /** 要改的团队——弹窗打开时照它起手。 */
  team: Team
  /** 正在保存：按钮转圈，这时候关不掉。 */
  busy?: boolean
  /** 整张表单那一句报错（没网、没权限）。 */
  error?: string
  /** 落在名字那一格的报错（没填、撞名）。 */
  nameError?: string
}>()
const emit = defineEmits<{
  save: [payload: { name: string; intro: string; avatarFile?: File }]
  /** 人在输入框里动了一下：外面把上一次那两句报错撤掉。 */
  clearErrors: []
}>()

const open = defineModel<boolean>()

const name = ref('')
const intro = ref('')
const avatarFile = ref<File>()

// 每次打开都从小队现在的样子起手：上一次取消留下的半截输入不该跟到下一次，
// 上一次的报错也一样。`immediate` 是为了「打开着被挂上去」也算一次起手。
watch(
  open,
  (isOpen) => {
    if (!isOpen) return
    name.value = props.team.name
    intro.value = props.team.intro ?? ''
    avatarFile.value = undefined
  },
  { immediate: true }
)

function save() {
  if (props.busy) return
  emit('save', { name: name.value, intro: intro.value, avatarFile: avatarFile.value })
}
</script>

<template>
  <AdaptiveDialog
    v-model="open"
    :title="t('work.teamProfile.editTitle')"
    :primary-label="t('work.teamProfile.save')"
    :primary-loading="busy"
    :close-disabled="busy"
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
            :error-messages="nameError"
            class="mb-4"
            rounded="md"
            @update:model-value="emit('clearErrors')"
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
