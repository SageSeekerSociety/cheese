/**
 * 预览站里「账号设置与项目工作台那一组整页」的一册分卷——账号设置那一组的后一半（密码与安全、模型连接、这台设备）。
 *
 * 它和同级的另外两份分卷一起，由 `catalogViewSettingsWorkspace.ts` 合并成
 * `VIEW_SETTINGS_WORKSPACE_ENTRIES` 交给 `catalogViews.ts`；各屏公用数据见
 * `catalogViewSettingsWorkspaceFixtures.ts`。
 */
import type { CatalogEntry, CatalogNeed } from './catalog'

import {
  DEVICE_TEAMS,
  MODEL_CONNECTIONS,
  MODEL_HELD,
  MODEL_SERVICE_DEVICES,
  SECURITY_DIALOGS,
  SECURITY_PROPS,
} from './catalogViewSettingsWorkspaceFixtures'

import { t } from '@/i18n'
import ModelConnectionsView from '@/views/user/settings/ModelConnectionsView.vue'
import SecurityView from '@/views/user/settings/SecurityView.vue'
import ThisDeviceView from '@/views/user/settings/ThisDeviceView.vue'

/** 画法要 Vuetify、文案要语言包。 */
const UI_T: CatalogNeed[] = ['vuetify', 'i18n']

export const VIEW_SETTINGS_WORKSPACE_ENTRIES_B: CatalogEntry[] = [
  {
    id: 'security-view',
    title: 'SecurityView',
    get about() {
      return t('catalog.securityView.about')
    },
    file: 'src/views/user/settings/SecurityView.vue',
    component: SecurityView,
    needs: UI_T,
    // 改密码、开两步验证那两只对话框画在 body 上。
    teleport: true,
    args: { ...SECURITY_PROPS, ...SECURITY_DIALOGS },
    states: [
      {
        get name() {
          return t('catalog.securityView.s1.name')
        },
        get note() {
          return t('catalog.securityView.s1.note')
        },
        props: {},
        // 通行密钥那一行详情只有「有通行密钥」时才画。
        expectSelector: '.srow--sub',
        expect: '通行密钥',
      },
      {
        get name() {
          return t('catalog.securityView.s2.name')
        },
        get note() {
          return t('catalog.securityView.s2.note')
        },
        props: { webAuthnSupported: false },
        expect: '当前浏览器不支持通行密钥',
        // 不支持时「添加」那颗画成按下不动的样子。
        expectSelector: '.settings-card button.v-btn--disabled',
      },
      {
        get name() {
          return t('catalog.securityView.s3.name')
        },
        get note() {
          return t('catalog.securityView.s3.note')
        },
        props: { totpEnabled: true, backupCodes: ['3f2a-11cd-90b8'] },
        expect: '已开启，使用验证器应用',
        expectSelector: '.status-dot--on',
      },
      {
        get name() {
          return t('catalog.securityView.s4.name')
        },
        get note() {
          return t('catalog.securityView.s4.note')
        },
        props: { connections: [], held: [] },
        expect: '暂无关联账号',
        // 第二张卡片（已关联账号）里的空态那一行。
        expectSelector: 'section.settings-card:nth-of-type(2) .srow--empty',
      },
      {
        get name() {
          return t('catalog.securityView.s5.name')
        },
        get note() {
          return t('catalog.securityView.s5.note')
        },
        props: { sessions: [] },
        expect: '暂无其他登录',
        // 第三张卡片（登录中的浏览器与 app）里的空态那一行。
        expectSelector: 'section.settings-card:nth-of-type(3) .srow--empty',
      },
      {
        get name() {
          return t('catalog.securityView.s6.name')
        },
        get note() {
          return t('catalog.securityView.s6.note')
        },
        props: { showChangePassword: true },
        expectSelector: '#security-new-password',
      },
      {
        get name() {
          return t('catalog.securityView.s7.name')
        },
        get note() {
          return t('catalog.securityView.s7.note')
        },
        props: { showTotp: true, setupStep: 'qr' },
        expect: 'JBSWY3DPEHPK3PXP',
        expectSelector: '.setup__qr',
      },
      {
        get name() {
          return t('catalog.securityView.s8.name')
        },
        get note() {
          return t('catalog.securityView.s8.note')
        },
        props: { showTotp: true, setupStep: 'verify' },
        expect: '输入应用中显示的 6 位验证码',
        expectSelector: '.setup .v-otp-input',
      },
      {
        get name() {
          return t('catalog.securityView.s9.name')
        },
        get note() {
          return t('catalog.securityView.s9.note')
        },
        props: { showTotp: true, setupStep: 'backup', backupCodes: ['3f2a-11cd-90b8', '77bb-20ef-3311'] },
        expect: '3f2a-11cd-90b8',
        expectSelector: '.setup__codes',
      },
      {
        get name() {
          return t('catalog.securityView.s10.name')
        },
        get note() {
          return t('catalog.securityView.s10.note')
        },
        props: {
          showCloseAccount: true,
          closeError:
            '这个账号是团队「数据结构课题组」唯一的所有者，这些团队里还有其他成员，或者别人还能进来（有待接受的邀请，或加入不需要审批）。先把所有权转让给其他成员，或取消邀请并开启「加入需要审批」，才能注销',
        },
        expect: '先把所有权转让给其他成员',
        expectSelector: '#security-close-username',
      },
    ],
  },
  {
    id: 'model-connections-view',
    title: 'ModelConnectionsView',
    get about() {
      return t('catalog.modelConnectionsView.about')
    },
    file: 'src/views/user/settings/ModelConnectionsView.vue',
    component: ModelConnectionsView,
    needs: UI_T,
    // 「解除」的确认框和「使用自定义模型服务」的表单都画在 body 上。
    teleport: true,
    args: {
      loading: false,
      error: null,
      connections: MODEL_CONNECTIONS,
      held: MODEL_HELD,
      devices: MODEL_SERVICE_DEVICES,
      busy: '',
      saved: 0,
    },
    states: [
      {
        get name() {
          return t('catalog.modelConnectionsView.s1.name')
        },
        get note() {
          return t('catalog.modelConnectionsView.s1.note')
        },
        props: { connections: [], held: [] },
        expect: '暂无模型连接',
        expectSelector: '.settings-empty',
      },
      {
        get name() {
          return t('catalog.modelConnectionsView.s2.name')
        },
        get note() {
          return t('catalog.modelConnectionsView.s2.note')
        },
        props: { connections: [{ ...MODEL_CONNECTIONS[2], logged_in: false, revoking: true }] },
        expect: '解除中',
        expectSelector: '.mc-state--pending',
      },
      {
        get name() {
          return t('catalog.modelConnectionsView.s3.name')
        },
        get note() {
          return t('catalog.modelConnectionsView.s3.note')
        },
        props: {
          connections: [],
          held: [{ ...MODEL_HELD[0], check_error: 'HTTP 401 invalid x-api-key' }],
        },
        expect: '检查未通过',
        expectSelector: '.mc-state--failed',
      },
      {
        get name() {
          return t('catalog.modelConnectionsView.s4.name')
        },
        get note() {
          return t('catalog.modelConnectionsView.s4.note')
        },
        props: { connections: MODEL_CONNECTIONS.filter((c) => c.device_id === 'dev-desk'), held: [] },
        expect: '实验室的 Pro',
      },
    ],
  },
  {
    id: 'this-device-view',
    title: 'ThisDeviceView',
    get about() {
      return t('catalog.thisDeviceView.about')
    },
    file: 'src/views/user/settings/ThisDeviceView.vue',
    component: ThisDeviceView,
    needs: UI_T,
    // 「断开」那颗的确认框画在 body 上。
    teleport: true,
    args: {
      loading: false,
      connected: true,
      online: true,
      connecting: false,
      name: '工作站',
      teams: DEVICE_TEAMS,
      teamIds: [2],
      claudeLoggedIn: true,
      claudePlan: 'max',
      claudeService: null,
      claudeState: 'idle',
      canUseModelService: true,
      error: null,
    },
    states: [
      {
        get name() {
          return t('catalog.thisDeviceView.s1.name')
        },
        get note() {
          return t('catalog.thisDeviceView.s1.note')
        },
        props: { loading: true },
        expectSelector: '[role="progressbar"]',
      },
      {
        get name() {
          return t('catalog.thisDeviceView.s2.name')
        },
        get note() {
          return t('catalog.thisDeviceView.s2.note')
        },
        props: { connected: false },
        expect: '未接入',
        expectSelector: '.this-device__empty',
      },
      {
        get name() {
          return t('catalog.thisDeviceView.s3.name')
        },
        get note() {
          return t('catalog.thisDeviceView.s3.note')
        },
        props: {},
        expect: '已接入 · 在线',
        expectSelector: '.settings-card .this-device__status--on',
      },
      {
        get name() {
          return t('catalog.thisDeviceView.s4.name')
        },
        get note() {
          return t('catalog.thisDeviceView.s4.note')
        },
        props: { online: false },
        expect: '已接入 · 离线',
        // 这一格没有「在线」那一颗亮点的形状。
        expectSelector: '.settings-card .this-device__status:not(.this-device__status--on)',
      },
      {
        get name() {
          return t('catalog.thisDeviceView.s5.name')
        },
        get note() {
          return t('catalog.thisDeviceView.s5.note')
        },
        props: { teamIds: [] },
        expect: '未提供给团队',
        expectSelector: '.this-device__chips .this-device__muted',
      },
      {
        get name() {
          return t('catalog.thisDeviceView.s6.name')
        },
        get note() {
          return t('catalog.thisDeviceView.s6.note')
        },
        props: { error: '这台设备的接入状态没读出来' },
        expect: '这台设备的接入状态没读出来',
        expectSelector: '[role="alert"]',
      },
    ],
  },
]
