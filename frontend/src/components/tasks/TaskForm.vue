<template>
  <v-form ref="taskForm" @submit.prevent="submitForm">
    <!-- 基本信息卡片 -->
    <v-card flat rounded="lg" class="mb-4 form-card">
      <v-card-item>
        <template #prepend>
          <div class="me-3">
            <v-avatar color="primary-lighten-5" size="48" class="elevation-0">
              <v-icon color="primary" size="28">mdi-information-outline</v-icon>
            </v-avatar>
          </div>
        </template>
        <v-card-title class="text-h5 ps-0">{{ t('tasks.form.basicInfo') }}</v-card-title>
      </v-card-item>

      <v-card-text class="pt-2">
        <v-row dense>
          <v-col cols="12">
            <v-text-field
              v-if="!parametersOnly"
              v-model="name"
              autocomplete="off"
              :label="t('tasks.form.taskName')"
              required
              v-bind="nameProps"
            ></v-text-field>
          </v-col>

          <v-col cols="12" md="6">
            <v-radio-group
              v-model="submitterType"
              :label="t('tasks.form.participantType')"
              required
              inline
              :disabled="isEditing"
              v-bind="submitterTypeProps"
              class="mt-0"
            >
              <v-radio :label="t('tasks.form.individual')" value="USER"></v-radio>
              <v-radio :label="t('tasks.form.team')" value="TEAM"></v-radio>
            </v-radio-group>
          </v-col>

          <v-col cols="12" md="6">
            <v-radio-group
              v-model="rank"
              :label="t('tasks.form.taskLevel')"
              required
              inline
              v-bind="rankProps"
              class="mt-0"
            >
              <v-radio :label="t('tasks.form.beginner')" :value="1"></v-radio>
              <v-radio :label="t('tasks.form.intermediate')" :value="2"></v-radio>
              <v-radio :label="t('tasks.form.advanced')" :value="3"></v-radio>
            </v-radio-group>
          </v-col>

          <v-col cols="12" md="6">
            <v-text-field
              v-model.number="participantLimit"
              :label="submitterType === 'TEAM' ? '队伍数量限制' : t('tasks.form.participantLimit')"
              type="number"
              min="1"
              v-bind="participantLimitProps"
              :hint="t('tasks.form.participantLimitHint')"
            >
              <template #append-inner>
                <v-icon size="small" color="primary">mdi-account-group</v-icon>
              </template>
            </v-text-field>
          </v-col>

          <!-- 小队人数限制 -->
          <template v-if="submitterType === 'TEAM'">
            <v-col cols="12" md="6">
              <v-select
                v-model="teamLockingPolicy"
                autocomplete="off"
                :label="t('tasks.form.teamLockingPolicy')"
                required
                v-bind="teamLockingPolicyProps"
                density="comfortable"
                :items="[
                  {
                    title: t('tasks.form.teamLockingPolicyNoLock'),
                    value: 'NO_LOCK',
                    description: t('tasks.form.teamLockingPolicyNoLockDesc'),
                  },
                  {
                    title: t('tasks.form.teamLockingPolicyLockOnApproval'),
                    value: 'LOCK_ON_APPROVAL',
                    description: t('tasks.form.teamLockingPolicyLockOnApprovalDesc'),
                  },
                ]"
                item-title="title"
                item-value="value"
              >
                <template #prepend-inner>
                  <v-icon size="small" color="primary">mdi-lock-outline</v-icon>
                </template>
                <template #item="{ item, props: slotProps }">
                  <v-list-item v-bind="slotProps">
                    <template #prepend>
                      <v-icon
                        :icon="item.raw && item.raw.value === 'NO_LOCK' ? 'mdi-lock-open-outline' : 'mdi-lock-outline'"
                        color="primary"
                        class="mr-2"
                      ></v-icon>
                    </template>
                    <v-list-item-subtitle v-if="item.raw" class="text-wrap">{{
                      item.raw.description
                    }}</v-list-item-subtitle>
                  </v-list-item>
                </template>
              </v-select>
            </v-col>

            <v-col cols="12" md="6">
              <v-text-field
                v-model.number="minTeamSize"
                label="最小队伍人数"
                type="number"
                required
                min="1"
                v-bind="minTeamSizeProps"
              >
                <template #append-inner>
                  <v-icon size="small" color="primary">mdi-account-multiple-outline</v-icon>
                </template>
              </v-text-field>
            </v-col>
            <v-col cols="12" md="6">
              <v-text-field
                v-model.number="maxTeamSize"
                label="最大队伍人数"
                type="number"
                required
                min="1"
                v-bind="maxTeamSizeProps"
              >
                <template #append-inner>
                  <v-icon size="small" color="primary">mdi-account-group</v-icon>
                </template>
              </v-text-field>
            </v-col>
            <v-col cols="12">
              <v-alert
                color="info"
                variant="tonal"
                density="comfortable"
                border="start"
                class="mt-2"
                icon="mdi-information-outline"
              >
                <div class="text-subtitle-2 font-weight-medium mb-1">{{ t('tasks.form.teamMemberManagement') }}</div>
                <p class="text-body-2 mb-0">
                  • 系统会在参与申请被审核通过时<strong>记录当前的队伍成员名单</strong><br />
                  • 无论选择哪种策略，最终的参与记录都只会基于审核通过时的成员名单<br />
                  • <strong>允许自由调整</strong>：队伍可以继续添加/移除成员，但这些变动不会影响已记录的参与情况<br />
                  • <strong>审核通过后锁定</strong>：队伍成员将被锁定，无法再进行任何成员变更
                </p>
              </v-alert>
            </v-col>
          </template>
        </v-row>
      </v-card-text>
    </v-card>

    <!-- 时间设置卡片 -->
    <v-card flat rounded="lg" class="mb-4 form-card">
      <v-card-item>
        <template #prepend>
          <div class="me-3">
            <v-avatar color="primary-lighten-5" size="48" class="elevation-0">
              <v-icon color="primary" size="28">mdi-clock-outline</v-icon>
            </v-avatar>
          </div>
        </template>
        <v-card-title class="text-h5 ps-0">{{ t('tasks.form.schedule') }}</v-card-title>
      </v-card-item>

      <v-card-text class="pt-2">
        <v-row dense>
          <v-col cols="12" md="6">
            <v-date-input
              v-model="registrationStartAt"
              :label="t('tasks.form.registrationStartAt')"
              density="comfortable"
              :required="false"
              v-bind="registrationStartAtProps"
              :allowed-dates="isAllowedDates"
              :hint="t('tasks.form.registrationStartAtHint')"
            ></v-date-input>
          </v-col>
          <v-col cols="12" md="6">
            <v-date-input
              v-model="deadline"
              :label="t('tasks.form.deadline')"
              clearable
              :hint="t('tasks.form.deadlineHint')"
              density="comfortable"
              v-bind="deadlineProps"
              :allowed-dates="isAllowedDates"
            ></v-date-input>
          </v-col>
          <v-col cols="12">
            <v-text-field
              v-model.number="defaultDeadline"
              :label="t('tasks.form.defaultDeadline')"
              type="number"
              required
              :prefix="t('tasks.form.defaultDeadlinePrefix')"
              :suffix="t('tasks.form.defaultDeadlineSuffix')"
              min="1"
              v-bind="defaultDeadlineProps"
            ></v-text-field>
          </v-col>
        </v-row>
      </v-card-text>
    </v-card>

    <!-- 分类标签 -->
    <v-card flat rounded="lg" class="mb-4 form-card">
      <v-card-item>
        <template #prepend>
          <div class="me-3">
            <v-avatar color="primary-lighten-5" size="48" class="elevation-0">
              <v-icon color="primary" size="28">mdi-tag-multiple</v-icon>
            </v-avatar>
          </div>
        </template>
        <v-card-title class="text-h5 ps-0">{{ t('tasks.form.classification') }}</v-card-title>
      </v-card-item>

      <v-card-text class="pt-2">
        <v-row>
          <v-col cols="12" md="6">
            <v-select
              v-if="categories.length > 0"
              v-model="categoryId"
              autocomplete="off"
              :items="categoryItems"
              :label="t('spaces.detail.tasks.category')"
              item-title="title"
              item-value="value"
              v-bind="categoryIdProps"
            >
              <template #prepend-inner>
                <v-icon size="small">mdi-shape</v-icon>
              </template>
            </v-select>
          </v-col>

          <v-col cols="12" md="6">
            <v-select
              v-model="topics"
              autocomplete="off"
              :items="topicItems"
              :label="t('spaces.detail.tasks.topic')"
              chips
              multiple
              v-bind="topicsProps"
            ></v-select>
          </v-col>
        </v-row>
      </v-card-text>
    </v-card>

    <!-- 实名信息需求配置 -->
    <v-card flat rounded="lg" class="mb-3 form-card">
      <v-card-item>
        <template #prepend>
          <div class="me-3">
            <v-avatar color="primary-lighten-5" size="48" class="elevation-0">
              <v-icon color="primary" size="28">mdi-shield-account</v-icon>
            </v-avatar>
          </div>
        </template>
        <v-card-title class="text-h5 ps-0">{{ t('tasks.form.realNameTitle') }}</v-card-title>
      </v-card-item>

      <v-card-text class="pt-2">
        <v-switch v-model="requireRealName" color="primary" hide-details v-bind="requireRealNameProps">
          <template #label>
            <div class="d-flex align-center">
              <v-icon
                :icon="requireRealName ? 'mdi-account-check' : 'mdi-account-outline'"
                :color="requireRealName ? 'primary' : 'medium-emphasis'"
                class="mr-2"
              ></v-icon>
              <span>{{ t('tasks.form.requireRealName') }}</span>
              <v-tooltip location="top">
                <template #activator="{ props }">
                  <v-icon size="small" color="primary" class="ml-2" v-bind="props">mdi-information-outline</v-icon>
                </template>
                <span>{{ t('tasks.form.requireRealNameHint') }}</span>
              </v-tooltip>
            </div>
          </template>
        </v-switch>

        <v-alert
          v-if="requireRealName"
          color="primary"
          variant="tonal"
          class="mt-3 mb-0"
          density="comfortable"
          border="start"
        >
          <div class="d-flex align-start">
            <v-avatar color="primary" class="mr-3 mt-1" size="28">
              <!-- 琥珀底上的反白图标：surface 在深色下是深墨，white 会糊在 #FFA733 上 -->
              <v-icon icon="mdi-shield-check" color="surface" size="18"></v-icon>
            </v-avatar>
            <div>
              <div class="text-subtitle-2 font-weight-medium mb-1">{{ t('tasks.form.realNameChosen.title') }}</div>
              <p class="text-body-2 mb-0">
                • {{ t('tasks.form.realNameChosen.line1') }}<br />
                • {{ t('tasks.form.realNameChosen.line2') }}<br />
                • {{ t('tasks.form.realNameChosen.line3') }}
              </p>
            </div>
          </div>
        </v-alert>

        <div v-else class="d-flex align-center mt-3">
          <v-icon color="medium-emphasis" icon="mdi-information-outline" class="mr-2"></v-icon>
          <span class="text-body-2 text-medium-emphasis">{{ t('tasks.form.realNameNotRequired') }}</span>
        </div>
      </v-card-text>
    </v-card>

    <!-- 权限设置 -->
    <v-card flat rounded="lg" class="mb-4 form-card">
      <v-card-item>
        <template #prepend>
          <div class="me-3">
            <v-avatar color="primary-lighten-5" size="48" class="elevation-0">
              <v-icon color="primary" size="28">mdi-shield-lock-outline</v-icon>
            </v-avatar>
          </div>
        </template>
        <v-card-title class="text-h5 ps-0">{{ t('tasks.form.accessControl.title') }}</v-card-title>
      </v-card-item>

      <v-card-text class="pt-2">
        <v-switch v-model="accessControlEnabled" color="primary" hide-details v-bind="accessControlEnabledProps">
          <template #label>
            <div class="d-flex align-center">
              <v-icon
                :icon="accessControlEnabled ? 'mdi-shield-check' : 'mdi-shield-outline'"
                :color="accessControlEnabled ? 'primary' : 'medium-emphasis'"
                class="mr-2"
              ></v-icon>
              <span>{{ t('tasks.form.accessControl.enableAccessRestriction') }}</span>
              <v-tooltip location="top">
                <template #activator="{ props: tooltipProps }">
                  <v-icon size="small" color="primary" class="ml-2" v-bind="tooltipProps"
                    >mdi-information-outline</v-icon
                  >
                </template>
                <span>{{ t('tasks.form.accessControl.enableAccessRestrictionHint') }}</span>
              </v-tooltip>
            </div>
          </template>
        </v-switch>

        <v-row v-if="accessControlEnabled" class="mt-4">
          <v-col cols="12">
            <v-select
              v-if="domainGroupItems.length > 0"
              v-model="accessDomainGroupIds"
              autocomplete="off"
              :items="domainGroupItems"
              :label="t('tasks.form.accessControl.domainGroups')"
              :hint="t('tasks.form.accessControl.domainGroupsHint')"
              chips
              multiple
              persistent-hint
              v-bind="accessDomainGroupIdsProps"
              item-title="title"
              item-value="value"
            >
              <template #prepend-inner>
                <v-icon size="small" color="primary">mdi-domain</v-icon>
              </template>
              <template #item="{ item, props: slotProps }">
                <v-list-item v-bind="slotProps">
                  <template #prepend>
                    <v-icon icon="mdi-web" color="primary" class="mr-2"></v-icon>
                  </template>
                  <template v-if="item.raw" #subtitle>
                    <span class="text-caption text-medium-emphasis">{{ item.raw.subtitle }}</span>
                  </template>
                </v-list-item>
              </template>
              <template #chip="{ item, props: chipProps }">
                <v-chip v-bind="chipProps" color="primary" variant="tonal" size="small">
                  <template #prepend>
                    <v-icon start size="x-small">mdi-web</v-icon>
                  </template>
                  {{ item.title }}
                </v-chip>
              </template>
            </v-select>
            <v-alert
              v-else
              color="warning"
              variant="tonal"
              density="comfortable"
              border="start"
              icon="mdi-alert-circle-outline"
            >
              {{ t('tasks.form.accessControl.noDomainGroups') }}
            </v-alert>
          </v-col>
        </v-row>
      </v-card-text>
    </v-card>

    <!-- 赛题详情 -->
    <v-card v-if="!parametersOnly" flat rounded="lg" class="mb-4 form-card">
      <v-card-item>
        <template #prepend>
          <div class="me-3">
            <v-avatar color="primary-lighten-5" size="48" class="elevation-0">
              <v-icon color="primary" size="28">mdi-text-box-outline</v-icon>
            </v-avatar>
          </div>
        </template>
        <v-card-title class="text-h5 ps-0">{{ t('tasks.form.taskDescription') }}</v-card-title>
      </v-card-item>

      <v-card-text class="pt-2">
        <!-- Markdown 格式使用纯文本编辑器 -->
        <v-textarea
          v-if="descriptionFormat === 'markdown'"
          v-model="markdownDescription"
          autocomplete="off"
          :label="t('tasks.form.markdownDescription')"
          :rows="10"
          :max-rows="30"
          rounded
          class="markdown-textarea"
        ></v-textarea>
        <!-- TipTap JSON 格式使用富文本编辑器 -->
        <TipTapEditor
          v-else
          ref="descriptionEditor"
          v-model="description"
          output="json"
          rounded
          :min-height="200"
          :max-height="1000"
          editor-class="tiptap-editor"
        />
      </v-card-text>
    </v-card>

    <!-- 视频链接 -->
    <v-card v-if="!parametersOnly" flat rounded="lg" class="mb-4 form-card">
      <v-card-item>
        <template #prepend>
          <div class="me-3">
            <v-avatar color="primary-lighten-5" size="48" class="elevation-0">
              <v-icon color="primary" size="28">mdi-video-outline</v-icon>
            </v-avatar>
          </div>
        </template>
        <v-card-title class="text-h5 ps-0">{{ t('tasks.form.videoTitle') }}</v-card-title>
      </v-card-item>

      <v-card-text class="pt-2">
        <v-text-field
          v-model="videoUrl"
          autocomplete="off"
          v-bind="videoUrlProps"
          :label="t('tasks.form.video.label')"
          placeholder="https://..."
          :hint="t('tasks.form.video.hint')"
          persistent-hint
        >
          <template #prepend-inner>
            <v-icon size="small" color="primary">mdi-link-variant</v-icon>
          </template>
        </v-text-field>
      </v-card-text>
    </v-card>

    <div class="d-flex justify-end">
      <slot name="buttons" :is-submitting="isSubmitting">
        <div class="d-flex gap-4">
          <v-btn v-if="isEditing" variant="text" :disabled="isSubmitting" @click="handleCancel">{{
            t('global.cancel')
          }}</v-btn>
          <v-btn type="submit" color="primary" size="large" :loading="isSubmitting">{{
            submitButtonText || t('global.submit')
          }}</v-btn>
        </div>
      </slot>
    </div>

    <!-- 隐私政策确认弹窗 -->
    <v-dialog v-model="privacyDialogOpen" max-width="600" persistent scrollable>
      <v-card rounded="lg">
        <v-card-title class="d-flex align-center px-4 pt-4 pb-2">
          <v-icon color="primary" class="mr-3" size="28">mdi-shield-check</v-icon>
          <span class="text-h5 font-weight-medium">{{ t('tasks.form.privacy.title') }}</span>
        </v-card-title>

        <v-card-text class="px-4 pb-2">
          <p class="text-subtitle-2 font-weight-medium mb-4">
            {{ t('tasks.form.privacy.intro') }}
          </p>

          <!-- 信息保护卡片 -->
          <v-card class="mb-5 privacy-protection-card" variant="flat" rounded="lg">
            <v-card-text class="pa-0">
              <v-row>
                <v-col cols="12" md="6">
                  <div class="d-flex align-start pa-3">
                    <v-avatar size="36" class="primary-soft mr-3">
                      <v-icon icon="mdi-eye-off" size="20" color="primary"></v-icon>
                    </v-avatar>
                    <div>
                      <div class="text-subtitle-2 font-weight-medium mb-1">
                        {{ t('tasks.form.privacy.anonymousTitle') }}
                      </div>
                      <p class="text-body-2 text-medium-emphasis mb-0">
                        {{ t('tasks.form.privacy.anonymousBody') }}
                      </p>
                    </div>
                  </div>
                </v-col>

                <v-col cols="12" md="6">
                  <div class="d-flex align-start pa-3">
                    <v-avatar size="36" class="primary-soft mr-3">
                      <v-icon icon="mdi-file-document-outline" size="20" color="primary"></v-icon>
                    </v-avatar>
                    <div>
                      <div class="text-subtitle-2 font-weight-medium mb-1">
                        {{ t('tasks.form.privacy.purposeTitle') }}
                      </div>
                      <p class="text-body-2 text-medium-emphasis mb-0">
                        {{ t('tasks.form.privacy.purposeBody') }}
                      </p>
                    </div>
                  </div>
                </v-col>

                <v-col cols="12" md="6">
                  <div class="d-flex align-start pa-3">
                    <v-avatar size="36" class="primary-soft mr-3">
                      <v-icon icon="mdi-shield-lock" size="20" color="primary"></v-icon>
                    </v-avatar>
                    <div>
                      <div class="text-subtitle-2 font-weight-medium mb-1">
                        {{ t('tasks.form.privacy.encryptionTitle') }}
                      </div>
                      <p class="text-body-2 text-medium-emphasis mb-0">
                        {{ t('tasks.form.privacy.encryptionBody') }}
                      </p>
                    </div>
                  </div>
                </v-col>

                <v-col cols="12" md="6">
                  <div class="d-flex align-start pa-3">
                    <v-avatar size="36" class="primary-soft mr-3">
                      <v-icon icon="mdi-history" size="20" color="primary"></v-icon>
                    </v-avatar>
                    <div>
                      <div class="text-subtitle-2 font-weight-medium mb-1">
                        {{ t('tasks.form.privacy.accessTitle') }}
                      </div>
                      <p class="text-body-2 text-medium-emphasis mb-0">
                        {{ t('tasks.form.privacy.accessBody') }}
                      </p>
                    </div>
                  </div>
                </v-col>
              </v-row>
            </v-card-text>
          </v-card>

          <!-- 使用场景 -->
          <div class="mb-4">
            <div class="text-subtitle-2 font-weight-medium mb-3">{{ t('tasks.form.privacy.scenariosTitle') }}</div>
            <v-row dense>
              <v-col cols="12" md="4">
                <v-card variant="flat" rounded="lg" class="privacy-usage-card h-100">
                  <v-card-text class="pa-3">
                    <div class="d-flex align-start h-100">
                      <v-avatar size="36" class="primary-soft mr-3 mt-1">
                        <v-icon icon="mdi-account-check" size="20" color="primary"></v-icon>
                      </v-avatar>
                      <div>
                        <div class="text-subtitle-2 font-weight-medium mb-1">
                          {{ t('tasks.form.privacy.verificationTitle') }}
                        </div>
                        <p class="text-body-2 text-medium-emphasis mb-0">
                          {{ t('tasks.form.privacy.verificationBody') }}
                        </p>
                      </div>
                    </div>
                  </v-card-text>
                </v-card>
              </v-col>

              <v-col cols="12" md="4">
                <v-card variant="flat" rounded="lg" class="privacy-usage-card h-100">
                  <v-card-text class="pa-3">
                    <div class="d-flex align-start h-100">
                      <v-avatar size="36" class="primary-soft mr-3 mt-1">
                        <v-icon icon="mdi-certificate-outline" size="20" color="primary"></v-icon>
                      </v-avatar>
                      <div>
                        <div class="text-subtitle-2 font-weight-medium mb-1">
                          {{ t('tasks.form.privacy.certificationTitle') }}
                        </div>
                        <p class="text-body-2 text-medium-emphasis mb-0">
                          {{ t('tasks.form.privacy.certificationBody') }}
                        </p>
                      </div>
                    </div>
                  </v-card-text>
                </v-card>
              </v-col>

              <v-col cols="12" md="4">
                <v-card variant="flat" rounded="lg" class="privacy-usage-card h-100">
                  <v-card-text class="pa-3">
                    <div class="d-flex align-start h-100">
                      <v-avatar size="36" class="primary-soft mr-3 mt-1">
                        <v-icon icon="mdi-trophy" size="20" color="primary"></v-icon>
                      </v-avatar>
                      <div>
                        <div class="text-subtitle-2 font-weight-medium mb-1">
                          {{ t('tasks.form.privacy.awardsTitle') }}
                        </div>
                        <p class="text-body-2 text-medium-emphasis mb-0">{{ t('tasks.form.privacy.awardsBody') }}</p>
                      </div>
                    </div>
                  </v-card-text>
                </v-card>
              </v-col>
            </v-row>
          </div>

          <!-- 合规承诺 -->
          <v-alert type="info" variant="tonal" class="privacy-rights-alert mb-3" border="start" density="comfortable">
            <div class="text-subtitle-2 font-weight-medium mb-1">{{ t('tasks.form.privacy.commitmentTitle') }}</div>
            <p class="text-body-2 mb-0">
              {{ t('tasks.form.privacy.commitmentBody') }}
            </p>
          </v-alert>
        </v-card-text>

        <v-card-actions class="pa-4 pt-2">
          <v-spacer></v-spacer>
          <v-btn color="secondary" variant="text" @click="cancelSubmitWithRealName">{{ t('global.cancel') }}</v-btn>
          <v-btn color="primary" variant="flat" @click="confirmSubmitWithRealName">{{
            t('tasks.form.privacy.understood')
          }}</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 视频链接无法解析确认 -->
    <v-dialog v-model="videoUrlDialogOpen" max-width="450" persistent>
      <v-card>
        <v-card-title class="text-h6">{{ t('tasks.form.video.dialogTitle') }}</v-card-title>
        <v-card-text>
          <v-alert type="warning" variant="tonal" class="mb-0">
            {{ t('tasks.form.video.dialogBody') }}
          </v-alert>
        </v-card-text>
        <v-card-actions class="pa-4 pt-0">
          <v-spacer></v-spacer>
          <v-btn variant="text" @click="cancelVideoUrlDialog">{{ t('global.cancel') }}</v-btn>
          <v-btn color="primary" variant="flat" @click="confirmVideoUrlDialog">{{
            t('tasks.form.video.dialogContinue')
          }}</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </v-form>
</template>

<script setup lang="ts">
import type { DomainGroup, TaskFormSubmitData, Topic } from '@/types'
import type { SpaceCategory } from '@/types'

import { computed, ref, toRefs, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { VDateInput } from 'vuetify/labs/VDateInput'
import { toTypedSchema } from '@vee-validate/zod'
import { useForm } from 'vee-validate'
import { z } from 'zod'

import { truncateString, vuetifyConfig } from '@/utils/form'

import TipTapEditor from '@/components/common/Editor/TipTapEditor.vue'

const isAllowedDates = (date: unknown) => {
  if (!(date instanceof Date)) return false
  const now = new Date()
  now.setHours(0, 0, 0, 0)
  date.setHours(0, 0, 0, 0)
  return date >= now
}

const props = withDefaults(
  defineProps<{
    initialData?: Partial<TaskFormSubmitData> | null
    submitButtonText?: string
    isEditing?: boolean
    classificationTopics: Topic[]
    categories?: SpaceCategory[]
    selectedCategoryId?: number
    domainGroups?: DomainGroup[]
    descriptionFormat?: 'markdown' | 'tiptap'
    originalDescription?: string
    parametersOnly?: boolean
  }>(),
  {
    initialData: null,
    // 不传就按默认那一个来（模板里是 submitButtonText || t('global.submit')）。
    // 从前这里是 '提交'，改成空缺省之后，按钮上的字才跟着语言走。
    submitButtonText: undefined,
    isEditing: false,
    classificationTopics: () => [],
    categories: () => [],
    selectedCategoryId: undefined,
    domainGroups: () => [],
    descriptionFormat: 'tiptap',
    originalDescription: '',
    parametersOnly: false,
  }
)

const { classificationTopics, categories } = toRefs(props)

const topicItems = computed(() => classificationTopics.value.map((topic) => ({ title: topic.name, value: topic.id })))
const categoryItems = computed(() => categories.value.map((category) => ({ title: category.name, value: category.id })))

const emit = defineEmits<{
  submit: [data: TaskFormSubmitData]
  cancel: []
}>()

const { t } = useI18n()

const taskForm = ref(null)
const descriptionEditor = ref<InstanceType<typeof TipTapEditor> | null>(null)

const { handleSubmit, defineField, isSubmitting } = useForm({
  validationSchema: toTypedSchema(
    z
      .object({
        name: z.string().min(1).max(100),
        submitterType: z.enum(['USER', 'TEAM']),
        registrationStartAt: z.date().optional().nullable(),
        deadline: z.date().nullable(),
        defaultDeadline: z.number().int().default(30),
        rank: z.number().int().min(1).max(3),
        topics: z.array(z.number()).optional(),
        categoryId: z.number().int().min(1, t('tasks.form.validation.categoryRequired')),
        minTeamSize: z.number().int().min(1).optional(),
        maxTeamSize: z.number().int().min(1).optional(),
        requireRealName: z.boolean().optional().default(false),
        participantLimit: z.number().int().min(1).optional().nullable(),
        teamLockingPolicy: z.enum(['NO_LOCK', 'LOCK_ON_APPROVAL']).optional(),
        accessControlEnabled: z.boolean().optional().default(false),
        accessDomainGroupIds: z.array(z.number()).optional(),
        videoUrl: z
          .string()
          .optional()
          .refine(
            (v) => {
              if (!v) return true
              try {
                const parsed = new URL(v)
                return parsed.protocol === 'https:'
              } catch {
                return false
              }
            },
            { message: t('tasks.form.validation.httpsRequired') }
          ),
      })
      .refine((arg) => !arg.maxTeamSize || !arg.minTeamSize || arg.maxTeamSize >= arg.minTeamSize, {
        message: '最大人数不能小于最小人数',
        path: ['maxTeamSize'],
      })
  ),
  initialValues: {
    ...(props.initialData ?? {}),
    name: props.initialData?.name ?? (props.parametersOnly ? t('tasks.form.pdfParametersName') : ''),
    registrationStartAt: props.initialData?.registrationStartAt
      ? new Date(props.initialData.registrationStartAt)
      : null,
    deadline: props.initialData?.deadline
      ? new Date(props.initialData.deadline)
      : props.isEditing
        ? null
        : new Date(Date.now() + 14 * 24 * 60 * 60 * 1000),
    requireRealName: props.initialData?.requireRealName ?? false,
    categoryId: props.initialData?.categoryId ?? props.selectedCategoryId ?? undefined,
    minTeamSize: props.initialData?.minTeamSize ?? 1,
    maxTeamSize: props.initialData?.maxTeamSize ?? 10,
    defaultDeadline: props.initialData?.defaultDeadline ?? 30,
    participantLimit: props.initialData?.participantLimit ?? null,
    teamLockingPolicy: props.initialData?.teamLockingPolicy ?? 'NO_LOCK',
    accessControlEnabled: props.initialData?.accessControlEnabled ?? false,
    accessDomainGroupIds: props.initialData?.accessDomainGroupIds ?? [],
    videoUrl: props.initialData?.videoUrl ?? '',
  },
})

const [name, nameProps] = defineField('name', vuetifyConfig)
const [submitterType, submitterTypeProps] = defineField('submitterType', vuetifyConfig)
const [rank, rankProps] = defineField('rank', vuetifyConfig)
const [registrationStartAt, registrationStartAtProps] = defineField('registrationStartAt', vuetifyConfig)
const [deadline, deadlineProps] = defineField('deadline', vuetifyConfig)
const [defaultDeadline, defaultDeadlineProps] = defineField('defaultDeadline', vuetifyConfig)
const [topics, topicsProps] = defineField('topics', vuetifyConfig)
const [minTeamSize, minTeamSizeProps] = defineField('minTeamSize', vuetifyConfig)
const [maxTeamSize, maxTeamSizeProps] = defineField('maxTeamSize', vuetifyConfig)
const [categoryId, categoryIdProps] = defineField('categoryId', vuetifyConfig)
const [requireRealName, requireRealNameProps] = defineField('requireRealName', vuetifyConfig)
const [participantLimit, participantLimitProps] = defineField('participantLimit', vuetifyConfig)
const [teamLockingPolicy, teamLockingPolicyProps] = defineField('teamLockingPolicy', vuetifyConfig)
const [accessControlEnabled, accessControlEnabledProps] = defineField('accessControlEnabled', vuetifyConfig)
const [accessDomainGroupIds, accessDomainGroupIdsProps] = defineField('accessDomainGroupIds', vuetifyConfig)
const [videoUrl, videoUrlProps] = defineField('videoUrl', vuetifyConfig)

const domainGroupItems = computed(
  () => props.domainGroups?.map((g) => ({ title: g.name, value: g.id, subtitle: g.domains.join(', ') })) ?? []
)

const createEmptyDescription = () => ({
  type: 'doc',
  content: [{ type: 'paragraph' }],
})

const description = ref(props.initialData?.description || createEmptyDescription())
// Markdown 格式的描述内容
const markdownDescription = ref(props.originalDescription || '')

const pendingSubmissionData = ref<{ descriptionText: string | undefined; values: any } | null>(null)

const isBilibiliUrl = (v: string): boolean => {
  if (!v) return true
  return /bilibili\.com\/video\/BV[\w]+/.test(v)
}

const submitForm = handleSubmit((values) => {
  if (requireRealName.value && !wasRealNameEnabled.value) {
    privacyDialogOpen.value = true
    pendingSubmissionData.value = {
      descriptionText: descriptionEditor.value?.editor?.getText(),
      values,
    }
    return
  }
  if (videoUrl.value && !isBilibiliUrl(videoUrl.value)) {
    videoUrlDialogOpen.value = true
    pendingSubmissionData.value = {
      descriptionText: descriptionEditor.value?.editor?.getText(),
      values,
    }
    return
  }
  submitFormData(values)
})

const submitFormData = (values: any) => {
  const descriptionText = pendingSubmissionData.value?.descriptionText ?? descriptionEditor.value?.editor?.getText()
  const deadlineDate = values.deadline ? new Date(values.deadline) : null
  const registrationStartAtDate = values.registrationStartAt ? new Date(values.registrationStartAt) : null
  deadlineDate?.setHours(23, 59, 59, 999)

  // 根据原始格式决定保存的描述内容
  let savedDescription: string
  let introText: string
  if (props.parametersOnly) {
    savedDescription = ''
    introText = ''
  } else if (props.descriptionFormat === 'markdown') {
    // 如果原始是 markdown 格式，保存纯文本内容
    savedDescription = markdownDescription.value || ''
    introText = markdownDescription.value || ''
  } else {
    // 如果原始是 TipTap JSON 格式，保存 JSON
    savedDescription = JSON.stringify(description.value)
    introText = descriptionText || ''
  }

  const submissionData: TaskFormSubmitData = {
    ...values,
    description: savedDescription,
    intro: truncateString(introText, 255),
    registrationStartAt: registrationStartAtDate ? registrationStartAtDate.getTime() : null,
    deadline: deadlineDate?.getTime() ?? null,
    ...(props.isEditing ? { hasDeadline: deadlineDate !== null } : {}),
    resubmittable: true,
    editable: true,
    requireRealName: requireRealName.value,
    categoryId: categoryId.value || undefined,
    minTeamSize: submitterType.value === 'TEAM' ? minTeamSize.value : undefined,
    maxTeamSize: submitterType.value === 'TEAM' ? maxTeamSize.value : undefined,
    participantLimit: participantLimit.value || undefined,
    teamLockingPolicy: submitterType.value === 'TEAM' ? teamLockingPolicy.value : undefined,
    accessControlEnabled: accessControlEnabled.value,
    accessDomainGroupIds: accessControlEnabled.value ? accessDomainGroupIds.value : undefined,
    videoUrl: videoUrl.value || null,
  }
  emit('submit', submissionData)
}

const privacyDialogOpen = ref(false)
const wasRealNameEnabled = ref(false)

const cancelSubmitWithRealName = () => {
  requireRealName.value = false
  wasRealNameEnabled.value = false
  privacyDialogOpen.value = false
  pendingSubmissionData.value = null
}

const confirmSubmitWithRealName = () => {
  wasRealNameEnabled.value = true
  privacyDialogOpen.value = false
  if (pendingSubmissionData.value) {
    submitFormData(pendingSubmissionData.value.values)
    pendingSubmissionData.value = null
  }
}

const videoUrlDialogOpen = ref(false)

const cancelVideoUrlDialog = () => {
  videoUrlDialogOpen.value = false
  pendingSubmissionData.value = null
}

const confirmVideoUrlDialog = () => {
  videoUrlDialogOpen.value = false
  if (pendingSubmissionData.value) {
    submitFormData(pendingSubmissionData.value.values)
    pendingSubmissionData.value = null
  }
}

// 初始化wasRealNameEnabled
wasRealNameEnabled.value = !!props.initialData?.requireRealName

watch(
  () => props.initialData?.name,
  (value) => {
    if (props.parametersOnly && value && value !== name.value) {
      name.value = value
    }
  },
  { immediate: true }
)

const handleCancel = () => {
  emit('cancel')
}
</script>

<style scoped>
.real-name-section {
  position: relative;
}

.real-name-card {
  border: 1px solid rgba(var(--v-border-color), 0.12);
  background-color: rgb(var(--v-theme-surface));
  transition: all 0.2s ease;
}

.real-name-option {
  min-width: 280px;
}

.privacy-notice {
  min-width: 320px;
  flex-grow: 1;
}

.primary-soft {
  background-color: rgba(var(--v-theme-primary), 0.08);
}

.privacy-protection-card {
  border: 1px solid rgba(var(--v-border-color), 0.12);
  background-color: rgb(var(--v-theme-surface));
}

.privacy-usage-card {
  border: 1px solid rgba(var(--v-border-color), 0.12);
  background-color: var(--surface);
  transition: all 0.2s ease;
}

.privacy-usage-card:hover {
  border-color: rgba(var(--v-theme-primary), 0.15);
  background-color: rgba(var(--v-theme-primary), 0.01);
}

.privacy-rights-alert {
  background-color: rgba(var(--v-theme-info), 0.05);
  border-color: rgba(var(--v-theme-info), 0.3);
}

.tiptap-editor {
  margin-top: 0;
}

.markdown-textarea :deep(.v-textarea__textarea) {
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', 'Consolas', monospace;
  font-size: 0.9rem;
  line-height: 1.6;
  resize: vertical;
}

.markdown-textarea :deep(.v-textarea__field) {
  min-height: 200px;
}

.form-card {
  border: 1px solid rgba(var(--v-border-color), 0.12);
  background-color: rgb(var(--v-theme-surface));
  transition: all 0.2s ease;
}

.form-card:hover {
  border-color: rgba(var(--v-theme-primary), 0.15);
  transform: translateY(-1px);
  box-shadow: 0 2px 8px rgba(var(--v-theme-primary), 0.05);
}

.v-card-text {
  padding-top: 12px;
  padding-bottom: 16px;
}

.mb-4 {
  margin-bottom: 12px !important;
}

.mb-3 {
  margin-bottom: 12px !important;
}

.mt-3 {
  margin-top: 12px !important;
}

.mt-4 {
  margin-top: 12px !important;
}
</style>
