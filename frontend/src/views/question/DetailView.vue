<template>
  <v-container>
    <v-row>
      <v-col>
        <!-- A failed (re)load must replace the card, not leave the previous question standing under the new URL (docs/design-system.md §3.10). -->
        <BaseLoadError
          v-if="failed"
          :title="t('questions.detail.loadFailed')"
          :error="failureReason"
          :forbidden="forbidden"
          @retry="$emit('retry')"
        />

        <v-card v-else-if="question" rounded="lg" flat>
          <v-card-item>
            <v-card-title class="text-h5" data-user-content>{{ question.title }}</v-card-title>
            <v-card-subtitle class="d-flex align-center question-info">
              <span>{{ t('questions.detail.createdAt', { time: createdAt }) }}</span>
              <span v-if="showUpdatedAt">{{ t('questions.detail.updatedAt', { time: updatedAt }) }}</span>
              <span>{{ t('questions.detail.viewCount', { count: question.view_count }) }}</span>
            </v-card-subtitle>
            <template #append>
              <BaseButton
                :kind="question.is_follow ? 'ghost' : 'secondary'"
                prepend-icon="mdi-plus"
                @click="toggleFollow"
              >
                <template v-if="question.is_follow">
                  {{ t('questions.detail.buttons.unfollow') }}
                </template>
                <template v-else>
                  {{
                    t(
                      'questions.detail.buttons.follow',
                      {
                        count: question.follow_count,
                      },
                      question.follow_count
                    )
                  }}
                </template>
              </BaseButton>
            </template>
          </v-card-item>
          <v-card-text>
            <div class="d-flex flex-wrap align-center mb-4" style="gap: 8px">
              <template v-for="topic in question.topics" :key="topic.id">
                <v-chip color="primary" label>{{ topic.name }}</v-chip>
              </template>
            </div>
            <div class="d-flex align-center mb-2">
              <user-avatar :avatar="getAvatarUrl(question.author.avatarId)" :size="24" />
              <span class="ms-2">{{ question.author.nickname }}</span>
            </div>
            <div class="d-flex align-center flex-wrap" style="gap: 8px">
              <v-tooltip v-if="question.bounty && question.bounty > 0" bottom>
                <template #activator="{ props: activatorProps }">
                  <v-chip v-bind="activatorProps" color="primary" class="mb-2" prepend-icon="mdi-currency-usd">
                    {{ t('questions.detail.bounty', { bounty: question.bounty }) }}
                    <template #append>
                      <v-icon>mdi-cheese</v-icon>
                      <v-icon size="12" class="ms-1">mdi-help-circle-outline</v-icon>
                    </template>
                  </v-chip>
                </template>
                <span>
                  {{
                    t('questions.detail.bountyTip', {
                      bounty: question.bounty,
                      before: dayjs(question.bounty_start_at).add(3, 'days').format('YYYY-MM-DD HH:mm'),
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
              :score="question.attitudes.difference"
              :current-vote="question.attitudes.user_attitude"
              class="me-2"
              @upvote="setAttitude(NewAttitudeType.Positive)"
              @downvote="setAttitude(NewAttitudeType.Negative)"
              @cancel-vote="setAttitude(NewAttitudeType.None)"
            />

            <v-dialog
              v-if="!question.accepted_answer && question.bounty && question.bounty > 0"
              width="auto"
              scrollable
            >
              <template #activator="{ props: activatorProps }">
                <BaseButton kind="secondary" prepend-icon="mdi-account-multiple-plus" v-bind="activatorProps">
                  {{ t('questions.detail.buttons.invite') }}
                </BaseButton>
              </template>

              <template #default="{ isActive }">
                <v-card>
                  <v-card-item>
                    <v-card-title class="text-h6">{{ t('questions.detail.inviteTitle') }}</v-card-title>
                  </v-card-item>
                  <v-card-text style="padding: 8px">
                    <div class="px-3 mb-2">
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
                    <invitation-list-view :users="inviteUsers" :is-invited="inviteInvited" @invite="invite" />
                  </v-card-text>

                  <v-card-actions>
                    <v-spacer></v-spacer>

                    <BaseButton kind="ghost" @click="isActive.value = false">{{
                      t('questions.detail.close')
                    }}</BaseButton>
                  </v-card-actions>
                </v-card>
              </template>
            </v-dialog>
            <v-dialog v-else-if="!question.accepted_answer" v-model="bountyDialog" :max-width="DIALOG_WIDTH.md">
              <template #activator="{ props: activatorProps }">
                <BaseButton kind="secondary" prepend-icon="mdi-currency-usd" v-bind="activatorProps">
                  {{ t('questions.detail.buttons.bounty') }}
                </BaseButton>
              </template>

              <template #default="{ isActive }">
                <v-card>
                  <v-card-item>
                    <v-card-title class="text-h6">{{ t('questions.detail.addBountyTitle') }}</v-card-title>
                  </v-card-item>
                  <v-card-text class="pa-1">
                    <div class="px-3 mb-2">
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
                    </div>
                  </v-card-text>

                  <v-card-actions>
                    <v-spacer></v-spacer>

                    <BaseButton kind="ghost" @click="isActive.value = false">{{ t('global.cancel') }}</BaseButton>
                    <BaseButton kind="primary" :loading="bountyLoading" @click="onAddBounty">{{
                      t('questions.detail.buttons.addBounty')
                    }}</BaseButton>
                  </v-card-actions>
                </v-card>
              </template>
            </v-dialog>

            <BaseButton kind="ghost" prepend-icon="mdi-comment-outline">
              {{ t('questions.detail.buttons.comment') }}
              <span v-if="question.comment_count">{{ question.comment_count }}</span>
            </BaseButton>
            <BaseButton kind="ghost" prepend-icon="mdi-star-outline">
              {{ t('questions.detail.buttons.favorite') }}
            </BaseButton>
          </v-card-actions>
        </v-card>

        <v-skeleton-loader v-else type="list-item-avatar, paragraph, button@2" />
      </v-col>
    </v-row>
    <v-row v-if="question?.accepted_answer">
      <v-col>
        <v-alert class="accept-alert" type="success">
          <template #title>
            <i18n-t keypath="questions.detail.acceptedAnswerTitle" tag="span">
              <template #user>
                <UserRef
                  :handle="question.accepted_answer.author.username"
                  :name="question.accepted_answer.author.nickname"
                  :to="resolveUser(question.accepted_answer.author.username).to"
                  @navigate="$emit('navigate', resolveUser(question.accepted_answer.author.username).to)"
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
                  questionId: question.id.toString(),
                  answerId: question.accepted_answer.id.toString(),
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
          <template v-if="question?.my_answer_id">
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
// 问题详情页（`/questions/:questionId`）**画的那一半**：只认 props、只发事件。
//
// 取数、读路由、页标题、`provide` 那道题、写操作（悬赏 / 发布回答 / 赞踩 / 关注 /
// 邀请 / 采纳链接）都在容器 `Detail.vue` 里；题主判断、人名去处（`resolveUser`）也由
// 它算好递进来。本地只留纯 UI 的开关（悬赏对话框、滑块值）与编辑器实例。
import type EditorJS from '@editorjs/editorjs'
import type { ResolvedUserRef } from '@/composables/useUserRefResolver'
import type { Question, User } from '@/types'

import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { toast } from 'vuetify-sonner'
import dayjs from 'dayjs'

import { defaultEditorConfig } from '@/utils/editor'
import { getAvatarUrl } from '@/utils/materials'
import { parse } from '@/utils/parser'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import { DIALOG_WIDTH } from '@/components/base/dialogSize'
import ContentVoter from '@/components/common/ContentVoter.vue'
import RichEditor from '@/components/common/Editor/Editor.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import UserRef from '@/components/common/UserRef.vue'
import InvitationListView from '@/components/questions/InvitationListView.vue'
import { NewAttitudeType } from '@/constants'

const { t } = useI18n()

const props = defineProps<{
  question: Question | null
  /** 路由上那道题的 id。回答子路由所在的那一行在它非零时才画。 */
  questionId: number
  /** 这一次没读到：`question` 为 null 是假的。 */
  failed: boolean
  failureReason: string | null
  /** 401/403：不给看，不给重试。 */
  forbidden: boolean
  /** 「加悬赏」那颗按钮转不转。 */
  bountyLoading: boolean
  /** 邀请弹窗里那份名单，与它一一对应的「已经请过了」。 */
  inviteUsers: User[]
  inviteInvited: boolean[]
  resolveUser: (handle: string | null | undefined) => ResolvedUserRef
  /** 发布回答：调接口、报成功、跳走；`true` 表示成功（这会儿才清空编辑器）。 */
  submitAnswer: (content: string) => Promise<boolean>
  /** 加悬赏；`true` 表示成功（这会关掉对话框）。 */
  addBounty: (amount: number) => Promise<boolean>
  setAttitude: (attitudeType: NewAttitudeType) => Promise<void>
  toggleFollow: () => Promise<void>
  openMyAnswer: () => void
  /** 请名单里第 index 位来回答。 */
  invite: (index: number) => Promise<void>
}>()

defineEmits<{
  retry: []
  navigate: [target: ResolvedUserRef['to']]
}>()

let editor: EditorJS
const onCreate = (editorInstance: EditorJS) => {
  editor = editorInstance
}

const addBountyInput = ref<number>(1)
const bountyDialog = ref(false)

const createdAt = computed(() => {
  if (props.question) {
    return dayjs(props.question.created_at).fromNow()
  }
  return ''
})
const showUpdatedAt = computed(() => {
  if (props.question) {
    return dayjs(props.question.created_at).isBefore(props.question.updated_at)
  }
  return false
})
const updatedAt = computed(() => {
  if (props.question) {
    return dayjs(props.question.updated_at).fromNow()
  }
  return ''
})

const contentHtml = computed(() => {
  if (props.question) {
    return parse(JSON.parse(props.question.content))
  }
  return ''
})

async function onAddBounty() {
  const ok = await props.addBounty(addBountyInput.value)
  if (ok) {
    bountyDialog.value = false
  }
}

const submit = async () => {
  const outputData = await editor.save()
  if (outputData.blocks.length === 0) {
    toast.error(t('questions.detail.postAnswerEmpty'))
    return
  }
  const ok = await props.submitAnswer(JSON.stringify(outputData))
  if (ok) {
    editor.clear()
  }
}

// 那颗「@名字」：显示名沿用原来传进来的 nickname，去处交给容器的 `resolveUser`。
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
