import type { RouteRecordRaw } from 'vue-router'

// Device-approve page. Public (no experimental gate): a `cheese link auto-connect` run
// prints <origin>/connect?code=…, which the enrolling human opens to bind the device to
// their account. Login is handled inside the page (redirects to SignIn and back).
const ConnectRoutes: RouteRecordRaw[] = [
  {
    path: '/connect',
    name: 'Connect',
    component: () => import('@/views/connect/ConnectView.vue'),
    meta: { title: '接入设备' },
  },
]

export default ConnectRoutes
