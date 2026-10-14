<template>
  <v-container>
    <v-row>
      <v-col>
        <!-- A failed (re)load must replace the card, not leave the previous question standing under the new URL (docs/design-system.md §3.10). -->
        <BaseLoadError
          v-if="loadFailed"
          :title="t('questions.detail.loadFailed')"
          :error="loadError"
          @retry="emit('retry-load')"
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
                @click="emit('toggle-follow-question')"
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
                :avatar="authorAvatarUrl"
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
              @upvote="emit('attitude-question', NewAttitudeType.Positive)"
              @downvote="emit('attitude-question', NewAttitudeType.Negative)"
              @cancel-vote="emit('attitude-question', NewAttitudeType.None)"
            />

            <v-dialog
              v-if="!questionData.accepted_answer && questionData.bounty && questionData.bounty > 0"
              v-model="inviteDialog"
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
                    <invitation-list
                      :people="invitePeople"
                      :invited-ids="invitedIds"
                      @invite="emit('invite', $event)"
                    />
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
            <v-dialog v-else-if="!questionData.accepted_answer" v-model="bountyDialog" :max-width="DIALOG_WIDTH.md">
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
                    <BaseButton kind="primary" :loading="bountyLoading" @click="emit('add-bounty')">{{
                      t('questions.detail.buttons.addBounty')
                    }}</BaseButton>
                  </v-card-actions>
                </v-card>
              </template>
            </v-dialog>

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
            <i18n-t scope="global" keypath="questions.detail.acceptedAnswerTitle" tag="span">
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
        <slot />
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
              <v-alert type="info" @click="emit('open-my-answer')">
                {{ t('questions.detail.postAnswerExist') }}
              </v-alert>
            </v-card-text>
          </template>
          <template v-else>
            <v-card-text>
              <rich-editor
                holder="editor"
                :config="{ ...defaultEditorConfig(), placeholder: t('questions.detail.postAnswerPlaceholder') }"
                @create="emit('create', $event)"
              />
            </v-card-text>
            <v-card-actions>
              <BaseButton kind="primary" @click="emit('submit')">{{
                t('questions.detail.buttons.postAnswer')
              }}</BaseButton>
            </v-card-actions>
          </template>
        </v-card>
      </v-col>
    </v-row>
  </v-container>
</template>

<script setup lang="ts">
// 题目详情那一页的画面：题卡（标题、关注、话题、提问者、悬赏、正文、投票）、采纳提示、
// 中间那一格留给被路由认出来的子页、底下发回答的编辑器。读哪道题、关注/投票/加悬赏/发回答
// 的请求、标题栏那行字都在容器 `Detail.vue` 那边，这里只收结果、把要点的事交上去。
import type EditorJS from '@editorjs/editorjs'
import type { Question, User } from '@/types'

import { useI18n } from 'vue-i18n'
import dayjs from 'dayjs'

import { defaultEditorConfig } from '@/utils/editor'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import { DIALOG_WIDTH } from '@/components/base/dialogSize'
import ContentVoter from '@/components/common/ContentVoter.vue'
import RichEditor from '@/components/common/Editor/Editor.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import UserRef from '@/components/common/UserRefLink.vue'
import InvitationList from '@/components/questions/InvitationList.vue'
import { NewAttitudeType } from '@/constants'

withDefaults(
  defineProps<{
    /** 读失败了：整张卡片换成错误块，不是一直停在骨架上。 */
    loadFailed: boolean
    loadError?: string | null
    /** 这道题；还没读回来是 `null`，画骨架屏。 */
    questionData?: Question | null
    /** 地址上那道题的编号，交给邀请名单用。 */
    questionId: number
    /** 正文（Editor.js 存的那份 JSON 已经解析成 HTML）。 */
    contentHtml: string
    /** 「提问于多久之前」那几段，容器那边算好的。 */
    createdAt: string
    updatedAt: string
    showUpdatedAt: boolean
    /** 提问者的头像地址；没挑过头像时是空串，由 UserAvatar 按 handle 画彩色首字母。 */
    authorAvatarUrl: string
    /** 加悬赏的请求在路上。 */
    bountyLoading: boolean
    /** 邀请弹窗里那份「可以邀请谁」的名单。 */
    invitePeople: User[]
    /** 已经邀请过的人的 id。 */
    invitedIds: number[]
  }>(),
  {
    loadError: null,
    questionData: null,
  }
)

const emit = defineEmits<{
  'retry-load': []
  'toggle-follow-question': []
  'attitude-question': [attitudeType: NewAttitudeType]
  'add-bounty': []
  'open-my-answer': []
  create: [editorInstance: EditorJS]
  submit: []
  invite: [user: User]
}>()

/** 加悬赏那个弹窗开没开、滑杆选了几枚 —— 弹窗自己画，值落在容器那边。 */
const bountyDialog = defineModel<boolean>('bountyDialog', { required: true })
const addBountyInput = defineModel<number>('addBountyInput', { required: true })

/** 邀请弹窗开没开：页面要知道，好趁弹窗开着把那两份名单读回来。 */
const inviteDialog = defineModel<boolean>('inviteDialog', { required: true })

const { t } = useI18n()
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
