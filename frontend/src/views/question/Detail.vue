<template>
  <v-container>
    <v-row>
      <v-col>
        <!-- A failed (re)load must replace the card, not leave the previous question standing under the new URL (docs/design-system.md §3.10). -->
        <BaseLoadError
          v-if="loadFailed"
          :title="t('questions.detail.loadFailed')"
          :error="loadError"
          @retry="retryLoad"
        />

        <v-card v-else-if="questionData" rounded="lg" flat>
          <v-card-item>
            <v-card-title class="text-h5" data-user-content>{{ questionData.title }}</v-card-title>
            <v-card-subtitle class="d-flex align-center question-info">
              <span>{{ t('questions.detail.createdAt', { time: createdAt }) }}</span>
              <span v-if="showUpdatedAt">{{ t('questions.detail.updatedAt', { time: updatedAt }) }}</span>
              <span>{{ t('questions.detail.viewCount', { count: questionData.view_count }) }}</span>
            </v-card-subtitle>
            <template #append>
              <BaseButton
                :kind="questionData.is_follow ? 'ghost' : 'secondary'"
                prepend-icon="mdi-plus"
                @click="toggleFollowQuestion"
              >
                <template v-if="questionData.is_follow">
                  {{ t('questions.detail.buttons.unfollow') }}
                </template>
                <template v-else>
                  {{
                    t(
                      'questions.detail.buttons.follow',
                      {
                        count: questionData.follow_count,
                      },
                      questionData.follow_count
                    )
                  }}
                </template>
              </BaseButton>
            </template>
          </v-card-item>
          <v-card-text>
            <div class="d-flex flex-wrap align-center mb-4" style="gap: 8px">
              <template v-for="topic in questionData.topics" :key="topic.id">
                <v-chip color="primary" label>{{ topic.name }}</v-chip>
              </template>
            </div>
            <div class="d-flex align-center mb-2">
              <user-avatar
                :avatar="isChosenAvatar(questionData.author.avatarId) ? getAvatarUrl(questionData.author.avatarId) : ''"
                :name="questionData.author.nickname"
                :seed="questionData.author.username"
                :size="24"
              />
              <span class="ms-2">{{ questionData.author.nickname }}</span>
            </div>
            <div class="d-flex align-center flex-wrap" style="gap: 8px">
              <v-tooltip v-if="questionData.bounty && questionData.bounty > 0" bottom>
                <template #activator="{ props }">
                  <v-chip v-bind="props" color="primary" class="mb-2" prepend-icon="mdi-currency-usd">
                    {{ t('questions.detail.bounty', { bounty: questionData.bounty }) }}
                    <template #append>
                      <v-icon>mdi-cheese</v-icon>
                      <v-icon size="12" class="ms-1">mdi-help-circle-outline</v-icon>
                    </template>
                  </v-chip>
                </template>
                <span>
                  {{
                    t('questions.detail.bountyTip', {
                      bounty: questionData.bounty,
                      before: dayjs(questionData.bounty_start_at).add(3, 'days').format('YYYY-MM-DD HH:mm'),
                    })
                  }}
                </span>
              </v-tooltip>
            </div>
            <!-- eslint-disable-next-line vue/no-v-html -->
            <div class="rich-content" v-html="contentHtml"></div>
          </v-card-text>
          <v-card-actions>
            <content-voter
              :score="questionData.attitudes.difference"
              :current-vote="questionData.attitudes.user_attitude"
              class="me-2"
              @upvote="attitudeQuestion(NewAttitudeType.Positive)"
              @downvote="attitudeQuestion(NewAttitudeType.Negative)"
              @cancel-vote="attitudeQuestion(NewAttitudeType.None)"
            />

            <BaseButton
              v-if="!questionData.accepted_answer && questionData.bounty && questionData.bounty > 0"
              kind="secondary"
              prepend-icon="mdi-account-multiple-plus"
              @click="inviteDialog = true"
            >
              {{ t('questions.detail.buttons.invite') }}
            </BaseButton>
            <BaseButton
              v-else-if="!questionData.accepted_answer"
              kind="secondary"
              prepend-icon="mdi-currency-usd"
              @click="bountyDialog = true"
            >
              {{ t('questions.detail.buttons.bounty') }}
            </BaseButton>

            <AdaptiveDialog
              v-if="!questionData.accepted_answer && questionData.bounty && questionData.bounty > 0"
              v-model="inviteDialog"
              :title="t('questions.detail.inviteTitle')"
              size="sm"
              :cancel-label="t('questions.detail.close')"
            >
              <div class="mb-2">
                <v-text-field
                  autocomplete="off"
                  clearable
                  :label="t('questions.detail.searchUsers')"
                  variant="outlined"
                  density="compact"
                  single-line
                  hide-details
                ></v-text-field>
              </div>
              <invitation-list :question-id="questionId" />
            </AdaptiveDialog>

            <AdaptiveDialog
              v-else-if="!questionData.accepted_answer"
              v-model="bountyDialog"
              :title="t('questions.detail.addBountyTitle')"
              :primary-label="t('questions.detail.buttons.addBounty')"
              :primary-loading="bountyLoading"
              @primary="addBounty"
            >
              <v-slider
                v-model="addBountyInput"
                thumb-label="always"
                min="1"
                max="20"
                step="1"
                show-ticks
                hide-details
              >
                <template #append>
                  <span style="vertical-align: baseline; min-width: 5rem; text-align: end">
                    <span>{{ t('questions.detail.bounty', { bounty: addBountyInput }) }} </span
                    ><v-icon>mdi-cheese</v-icon>
                  </span>
                </template>
              </v-slider>
            </AdaptiveDialog>

            <BaseButton kind="ghost" prepend-icon="mdi-comment-outline">
              {{ t('questions.detail.buttons.comment') }}
              <span v-if="questionData.comment_count">{{ questionData.comment_count }}</span>
            </BaseButton>
            <BaseButton kind="ghost" prepend-icon="mdi-star-outline">
              {{ t('questions.detail.buttons.favorite') }}
            </BaseButton>
          </v-card-actions>
        </v-card>

        <v-skeleton-loader v-else type="list-item-avatar, paragraph, button@2" />
      </v-col>
    </v-row>
    <v-row v-if="questionData?.accepted_answer">
      <v-col>
        <v-alert class="accept-alert" type="success">
          <template #title>
            <i18n-t keypath="questions.detail.acceptedAnswerTitle" tag="span">
              <template #user>
                <UserRef
                  :handle="questionData.accepted_answer.author.username"
                  :name="questionData.accepted_answer.author.nickname"
                />
              </template>
            </i18n-t>
          </template>
          <template #append>
            <BaseButton
              kind="secondary"
              :to="{
                name: 'QuestionAnswer',
                params: {
                  questionId: questionData.id.toString(),
                  answerId: questionData.accepted_answer.id.toString(),
                },
              }"
            >
              {{ t('questions.detail.buttons.viewAcceptedAnswer') }}
            </BaseButton>
          </template>
        </v-alert>
      </v-col>
    </v-row>
    <v-row v-if="questionId">
      <v-col>
        <router-view />
      </v-col>
    </v-row>
    <v-row>
      <v-col>
        <v-card flat rounded="lg" style="overflow: initial; z-index: initial">
          <v-card-item>
            <v-card-title class="text-h6">{{ t('questions.detail.postAnswerTitle') }}</v-card-title>
          </v-card-item>
          <template v-if="questionData?.my_answer_id">
            <v-card-text>
              <v-alert type="info" @click="openMyAnswer">
                {{ t('questions.detail.postAnswerExist') }}
              </v-alert>
            </v-card-text>
          </template>
          <template v-else>
            <v-card-text>
              <rich-editor
                holder="editor"
                :config="{ ...defaultEditorConfig(), placeholder: t('questions.detail.postAnswerPlaceholder') }"
                @create="onCreate"
              />
            </v-card-text>
            <v-card-actions>
              <BaseButton kind="primary" @click="submit">{{ t('questions.detail.buttons.postAnswer') }}</BaseButton>
            </v-card-actions>
          </template>
        </v-card>
      </v-col>
    </v-row>
  </v-container>
</template>

<script setup lang="ts">
import type EditorJS from '@editorjs/editorjs'
import type { Question } from '@/types'

import { computed, onMounted, provide, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { onBeforeRouteUpdate, useRoute } from 'vue-router'
import { useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'
import dayjs from 'dayjs'

import { defaultEditorConfig } from '@/utils/editor'
import { getAvatarUrl } from '@/utils/materials'
import { parse } from '@/utils/parser'

import { ensureDefaultAvatarId, isChosenAvatar } from '@/composables/useChosenAvatar'
import { usePageTitle } from '@/composables/usePageTitle'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import ContentVoter from '@/components/common/ContentVoter.vue'
import RichEditor from '@/components/common/Editor/Editor.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import UserRef from '@/components/common/UserRefLink.vue'
import InvitationList from '@/components/questions/InvitationList.vue'
import { NewAttitudeType } from '@/constants'
import { questionDataInjectionKey } from '@/keys'
import { AnswersApi } from '@/network/api/answers'
import { QuestionApi } from '@/network/api/questions'

const { t } = useI18n()
let editor: EditorJS
const onCreate = (editorInstance: EditorJS) => {
  editor = editorInstance
}

const route = useRoute()
const router = useRouter()
const { setDynamicTitle } = usePageTitle()

const addBountyInput = ref<number>(1)
const inviteDialog = ref(false)
const bountyDialog = ref(false)
const bountyLoading = ref(false)

const questionId = computed(() => parseInt(route.params.questionId as string))

const questionData = ref<Question | null>(null)
// 读失败与「还没加载出来」是两件事：失败替换掉这张卡片，不再一直停在骨架上。
const loadFailed = ref(false)
const loadError = ref<string | null>(null)

provide(questionDataInjectionKey, questionData)

// 提问者的头像：注册时 profile 一律被填上默认头像 id，不能直接 getAvatarUrl——那样
// 没挑过头像的人会长着和所有人同一张脸。先问一次全局默认头像 id（幂等，全进程共用一
// 份），再据此判断这条 id 是不是本人真挑的；不是就把空串交给 UserAvatar，由它按 handle
// 画彩色首字母。默认 id 到货时这个 ref 一变，模板跟着重算。
ensureDefaultAvatarId()

const createdAt = computed(() => {
  if (questionData.value) {
    return dayjs(questionData.value.created_at).fromNow()
  }
  return ''
})
const showUpdatedAt = computed(() => {
  if (questionData.value) {
    return dayjs(questionData.value.created_at).isBefore(questionData.value.updated_at)
  }
  return false
})
const updatedAt = computed(() => {
  if (questionData.value) {
    return dayjs(questionData.value.updated_at).fromNow()
  }
  return ''
})

const contentHtml = computed(() => {
  if (questionData.value) {
    return parse(JSON.parse(questionData.value.content))
  }
  return ''
})

const addBounty = async () => {
  bountyLoading.value = true
  try {
    await QuestionApi.addBounty(questionData.value!.id, addBountyInput.value)
    toast.success(t('questions.detail.addBountySuccess'))
    bountyDialog.value = false
  } catch (error) {
    toast.error(`${error}`)
  } finally {
    bountyLoading.value = false
  }
}

const load = async (id: number) => {
  loadFailed.value = false
  loadError.value = null
  try {
    const {
      data: { question },
    } = await QuestionApi.detail(id)
    questionData.value = question
  } catch (error) {
    console.error('Failed to load the question', error)
    loadFailed.value = true
    loadError.value = error instanceof Error && error.message ? error.message : null
  }
}

const retryLoad = () => load(questionId.value)

const submit = async () => {
  try {
    const outputData = await editor.save()
    if (outputData.blocks.length === 0) {
      toast.error(t('questions.detail.postAnswerEmpty'))
      return
    }
    const content = JSON.stringify(outputData)
    const { data } = await AnswersApi.answerQuestion(questionData.value!.id, content)
    toast.success(t('questions.detail.postAnswerSuccess'))
    editor.clear()
    router.push({
      name: 'QuestionAnswer',
      params: { questionId: questionData.value!.id.toString(), answerId: data.id.toString() },
    })
  } catch (error) {
    toast.error(`${error}`)
  }
}

const attitudeQuestion = async (attitudeType: NewAttitudeType) => {
  const {
    data: { attitudes },
  } = await QuestionApi.attitudeQuestion(questionData.value!.id, attitudeType)
  questionData.value!.attitudes = attitudes
}

const toggleFollowQuestion = async () => {
  if (questionData.value!.is_follow) {
    const {
      data: { follow_count: followCount },
    } = await QuestionApi.unfollowQuestion(questionData.value!.id)
    questionData.value!.follow_count = followCount
    questionData.value!.is_follow = false
  } else {
    const {
      data: { follow_count: followCount },
    } = await QuestionApi.followQuestion(questionData.value!.id)
    questionData.value!.follow_count = followCount
    questionData.value!.is_follow = true
  }
}

const openMyAnswer = () => {
  if (questionData.value?.my_answer_id) {
    router.push({
      name: 'QuestionAnswer',
      params: { questionId: questionData.value.id.toString(), answerId: questionData.value.my_answer_id.toString() },
    })
  }
}

onMounted(async () => {
  await load(questionId.value)
})

onBeforeRouteUpdate(async (to, from) => {
  if (to.params.questionId !== from.params.questionId) {
    await load(parseInt(to.params.questionId as string))
  }
})

watch(questionData, (newVal) => {
  if (newVal) {
    setDynamicTitle(newVal.title)
  }
})
</script>

<style lang="scss">
.ce-block__content,
.ce-toolbar__content {
  max-width: unset;
}

.ce-block__content {
  padding: 0;
}

.question-info {
  gap: 8px;
}

.accept-alert .v-alert__prepend {
  align-self: stretch;
}
</style>
