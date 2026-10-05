// The two 原理分解 figures' facts: what the stations are, and what happens at
// each one on the way past.
//
// Everything a reader sees here was read out of the code first — the routes in
// `backend/app/api/routes/llm_proxy.py`, the metering proxy in
// `deploy/metering-proxy/`, the model tunnel in
// `backend/app/domain/agent/machine_tunnel.py`, the device layers in
// `backend/app/domain/agent/device_*.py`, `place.py`, `host_failure.py`,
// `dispatch_log.py`. The numbers and the wire names are NOT written here: they
// come from `gen/arch_facts.py`, which greps them out of those files and whose
// output build.mjs holds this module to, so a port that moves or a placeholder
// that is renamed fails the build instead of leaving the picture telling the
// old story.
//
// `config(kind, f)` returns the whole figure, stations and walks both; the walk
// functions take the facts so a fact that disappears is a build error here,
// not an empty cell in the page.

// ---------- llm: one model request, session process to vendor ----------
const LLM_STATIONS = [
  { key: 'session', label: '会话进程', sub: '沙盒容器 / 裸进程 / 云机器 / Codex·Pi', col: 0, row: 2, tone: '--chart-1' },
  { key: 'helper', label: '隧道助手', sub: '云机器上的回环口', col: 1, row: 4, tone: '--chart-4' },
  { key: 'llmv1', label: '/llm/v1', sub: '主 API 的另一条入口', col: 2, row: 0, tone: '--chart-5' },
  { key: 'proxy443', label: ':443 反向代理', sub: '容器路径', col: 2, row: 1, tone: '--chart-4' },
  { key: 'proxy8444', label: ':8444 CONNECT', sub: '裸进程路径', col: 2, row: 3, tone: '--chart-4' },
  { key: 'meter', label: '计量代理', sub: '唯一的拦截点', col: 3, row: 2, tone: '--chart-3' },
  { key: 'admission', label: '准入', sub: 'POST /llm/admission', col: 3, row: 4, tone: '--chart-2' },
  { key: 'gateway', label: '网关路', sub: 'LiteLLM + 虚拟 key', col: 4, row: 1, tone: '--chart-5' },
  { key: 'subscription', label: '订阅路', sub: '平台的订阅凭证', col: 4, row: 3, tone: '--chart-6' },
  { key: 'vendor', label: '模型厂商', sub: '流式 token 回来', col: 5, row: 2, tone: '--faint' },
]

const LLM_WIRES = [
  ['session', 'proxy443'], ['session', 'proxy8444'], ['session', 'helper'], ['session', 'llmv1'],
  ['helper', 'proxy8444'],
  ['proxy443', 'meter'], ['proxy8444', 'meter'],
  // The meter asks admission and then acts on the answer, so a walk stands on
  // the admission stop and next on the route the answer chose. Those two hops
  // are the ANSWER steering the request, not a service it is handed to — but
  // the walk needs a line to travel along, so they are declared like the rest.
  ['meter', 'admission'], ['admission', 'gateway'], ['admission', 'subscription'],
  ['meter', 'gateway'], ['meter', 'subscription'],
  ['llmv1', 'gateway'],
  ['gateway', 'vendor'], ['subscription', 'vendor'],
]

const LLM_ENTRIES = [
  { key: 'sandbox', label: '沙盒容器', sub: '本机容器，:443 反向代理' },
  { key: 'bare', label: '裸进程', sub: '设备屏幕 / 云机器，:8444 CONNECT' },
  { key: 'cloud', label: '云机器', sub: '经模型隧道回到主机的计量代理' },
  { key: 'codex', label: 'Codex、Pi', sub: '{平台地址}/llm/v1' },
]

const LLM_SCENES = [
  { key: 'ok', label: '正常放行', tone: '--ok' },
  { key: 'budget', label: '额度用完', tone: '--warn' },
  { key: 'binding', label: '绑定解析不出', tone: '--warn' },
  { key: 'failopen', label: '问不到主 API', tone: '--chart-4' },
  { key: 'subagent', label: '分身指定模型', tone: '--chart-1' },
]

const LLM_MATRIX = {
  sandbox: ['ok', 'budget', 'binding', 'failopen', 'subagent'],
  bare: ['ok', 'budget', 'binding', 'failopen', 'subagent'],
  cloud: ['ok', 'budget', 'binding', 'failopen'],
  codex: ['ok', 'budget'],
}

// 会话进程里那一站的两种长相：容器/裸进程把占位凭证放在 authorization 上，
// Codex、Pi 带着自己的短期令牌去 /llm/v1。
const sessionStop = (f, entry) => ({
  at: 'session',
  head: '会话进程：发出一次 /v1/messages',
  act: '发起请求',
  see: [
    ['请求目标', 'api.anthropic.com:443'],
    ['authorization', `Bearer ${f.placeholder_token}`],
    ['body.model', f.sub_model.id],
  ],
  note: entry === 'codex'
    ? 'Codex、Pi 不能用 HTTPS_PROXY 引流，平台干脆把它们指向 {平台地址}/llm/v1。'
    : `会话里的凭证是一个占位 token（${f.placeholder_token}）：它不认证任何东西，能用的是平台上那一份。`,
})

const containerEntryStop = (f) => ({
  at: 'proxy443',
  head: '计量代理的 :443 反向代理：容器从这里进来',
  see: [['来源', `本机 docker 网桥 172.17.0.1:${f.ports.reverse}`], ['上游', 'api.anthropic.com']],
  say: [['它做的事', '认出发往模型厂商的请求，交给计量代理']],
  note: `容器没有 root，改不了 HTTPS_PROXY，但能改域名解析：--add-host 让 api.anthropic.com 指向计量代理，容器以为自己直连厂商。这个监听只在机器本机的网桥上开着。`,
})

const bareEntryStop = (f) => ({
  at: 'proxy8444',
  head: `计量代理的 :${f.ports.connect} CONNECT：裸进程从这里进来`,
  see: [['入口', `HTTPS_PROXY=http://172.17.0.1:${f.ports.connect}`], ['Proxy-Authorization', 'Basic <会话的短期芝士令牌>']],
  say: [['它做的事', '口令验过才放行，然后交给计量代理']],
  note: `裸进程没有 root，改不了域名解析，只能走 HTTPS_PROXY。CONNECT 时要拿短期令牌当代理密码：不然一个敞开的监听口就是给谁都能用的代理，验不过回 ${f.connect_refusal}。`,
})

const cloudHelperStop = (f) => ({
  at: 'helper',
  head: '机器上的隧道助手：HTTPS_PROXY 指向回环口',
  see: [['HTTPS_PROXY', 'http://127.0.0.1:<隧道端口>'], ['机器上有什么', '只有自己的短期芝士令牌']],
  say: [['它做的事', `把这次 CONNECT 装进一条 WebSocket，发到平台的 ${f.paths.tunnel}`]],
  note: '云机器没有 root、没有 docker、没有可以改的域名解析，但它能开一条出去的连接。助手就装在机器上，替它说 CONNECT。',
})

const cloudArriveStop = (f) => ({
  at: 'proxy8444',
  head: `到达的是同一个 :${f.ports.connect} 监听口`,
  see: [['从哪来', `模型隧道 ${f.paths.tunnel}（WebSocket）`], ['平台侧', '隧道由单独一个进程持有，发版不断']],
  say: [['收到的', '一个普通的 CONNECT']],
  note: '计量代理不关心这个 CONNECT 是从本机来的还是从隧道里出来的：入口只有一个，改写和记账都只有一份。',
})

const gatewayStop = () => ({
  at: 'gateway',
  head: '网关路：换成项目的虚拟 key，去 LiteLLM',
  see: [['authorization', 'Bearer <会话的占位凭证>'], ['x-api-key', '（没有）']],
  out: [['host', 'LiteLLM 网关'], ['authorization', 'Bearer <项目虚拟 key>'], ['x-api-key', '（删掉）']],
  note: '会话自己的凭证不跟着走：它不该落进另一个服务的日志里。网关按这个 key 记账，key 上带着 max_budget，超过就拒绝。',
})

const subscriptionStop = (f) => ({
  at: 'subscription',
  head: '订阅路：换成平台的订阅凭证',
  see: [['authorization', `Bearer ${f.placeholder_token}`]],
  out: [['authorization', 'Bearer <平台的订阅凭证>'], ['x-api-key', '（删掉）']],
  note: '占位凭证只有这一处会被换成真凭证，而且只在这个进程里换得出：模型侧凭据不上机器。请求正文除模型名外不改——订阅要求客户端就是 Claude Code 本身。',
})

const vendorStop = (f, { blocked = false, note } = {}) => ({
  at: 'vendor',
  head: blocked ? '模型厂商：这一轮没有请求到达' : '模型厂商：流式返回',
  ...(blocked
    ? { say: [['上游请求', '没有发出去']] }
    : { see: [['authorization', '<平台那一侧的凭据>']], say: [['status', '200'], ['返回', 'token 一块一块流回会话']] }),
  quota: blocked ? { label: '本项目额度', from: '0%', to: '0%' } : { label: '本项目额度', from: '62%', to: '58%' },
  note: note || '一次模型调用到这里结束：正文除了模型名一个字没改，响应原样流回去。',
  link: '/dev/llm#routes',
})

const admissionStop = (f, ok) => ({
  at: 'admission',
  head: '准入：能不能跑、走哪条路、用哪个模型名',
  act: '每请求一问',
  see: [['Bearer', '会话的短期芝士令牌（项目从令牌里的声明认，不看请求头）']],
  say: [
    ['allow', String(ok.allow)],
    ['reason', ok.reason],
    ['reason_kind', ok.reason_kind],
    ['supply.pool', ok.pool],
    ['supply.model', ok.model],
    ...(ok.key ? [['supply.key', '<项目虚拟 key>']] : []),
  ],
  quota: ok.quota,
  block: ok.block === true,
  // A fail-open answer is the meter's own invention, so the picture says so
  // («这一站是软的：放行») instead of drawing it as a decision that was made.
  soft: ok.soft === true,
  note: ok.note,
  link: '/dev/llm#admission',
})

const meterStop = (f, on) => ({
  at: 'meter',
  head: '计量代理：问准入、改写、再放行',
  see: on.see || [['authorization', `Bearer ${f.placeholder_token}`], ['body.model', on.seenModel || f.sub_model.id]],
  ...(on.out ? { out: on.out } : {}),
  say: on.say,
  quota: on.quota,
  note: on.note,
  link: '/dev/llm#one-exit',
})

const ASK = { pool: 'gateway', model: 'claude-sonnet-5' }
const Q = (from, to) => ({ label: '本项目额度', from, to })

const llmWalks = {
  sandbox: {
    ok: (f) => [
      sessionStop(f, 'sandbox'),
      containerEntryStop(f),
      meterStop(f, {
        say: [['先做的事', `POST ${f.paths.admission}`], ['准入回答', 'allow: true，pool: gateway']],
        quota: Q('62%', '62%'),
        note: '它是唯一的拦截点：每个 /v1/messages 都先问一次主 API 的准入，再决定怎么改写。',
      }),
      admissionStop(f, { allow: true, reason: f.budget.allow_reason, reason_kind: 'budget', pool: ASK.pool, model: ASK.model, key: true, quota: Q('62%', '62%'), note: '三个回答一起回来：能不能跑、走哪条路、模型名写什么。每个请求现查，所以改绑模型不用重启会话。' }),
      gatewayStop(),
      vendorStop(f),
    ],
    budget: (f) => [
      sessionStop(f, 'sandbox'),
      containerEntryStop(f),
      meterStop(f, {
        say: [['准入回答', 'allow: false，reason_kind: budget']],
        out: [['status', String(f.budget.status)], ['body', `{"type":"${f.budget.type}","message":"${f.budget.prefix}${f.budget.refusal_reason}"}`]],
        note: `准入说不行，计量代理就照这个拒绝的形状回给会话：${f.budget.status} ${f.budget.type}。`,
      }),
      admissionStop(f, { allow: false, reason: f.budget.refusal_reason, reason_kind: 'budget', pool: 'gateway（没给 key）', model: ASK.model, block: true, quota: Q('0%', '0%'), note: '额度用完。这一轮不执行，话题里会出现一条平台提示，厂商那边一个请求都没到。拒绝的形状由 reason_kind 决定，不是由额度这一件事决定。' }),
    ],
    binding: (f) => [
      sessionStop(f, 'sandbox'),
      containerEntryStop(f),
      meterStop(f, {
        say: [['准入回答', 'allow: false，reason_kind: binding']],
        out: [['status', String(f.binding.status)], ['body', `{"type":"${f.binding.type}","message":"…能指定的模型：…"}`]],
        note: `绑定解析不出来时说成「额度用完」会把人送去充值，而卡本身是坏的。所以这一类回 ${f.binding.status} ${f.binding.type}，客户端不会白白重试。`,
      }),
      admissionStop(f, { allow: false, reason: '这个项目不能跑 <模型名>；能指定的有：…', reason_kind: 'binding', pool: '（空）', model: '（没有）', block: true, note: '绑定的模型解析不出来时不会悄悄换到另一条路：回答或拒绝，不换池子，厂商那边一个请求都没到。' }),
    ],
    failopen: (f) => [
      sessionStop(f, 'sandbox'),
      containerEntryStop(f),
      meterStop(f, {
        say: [['准入', '连不上主 API'], ['它自己的决定', '放行（fail-open）']],
        note: '准入这一道是软的：问不到就放行。主 API 发版重启的那几秒不该变成平台不能干活。',
      }),
      { ...admissionStop(f, { allow: true, reason: f.admission.fail_open_reason, reason_kind: 'budget', pool: 'subscription', model: '（没说，用客户端自己的）', soft: true, note: '这一份判定是计量代理自己造的：每个字段都是默认值，pool 也读成订阅路。所以谁都不能拿它当「这个项目走网关」的依据。' }), block: false },
      subscriptionStop(f),
      { ...vendorStop(f), note: '兜底的不是准入：订阅路自己有一条滚动 token 上限，窗口内用超了回 429。' },
    ],
    subagent: (f) => [
      { ...sessionStop(f, 'sandbox'), head: '分身进程：请求头带着它要的模型', see: [['authorization', `Bearer ${f.placeholder_token}`], ['body.model', f.sub_model.id], ['x-cheese-child-model', f.sub_model.id]] },
      containerEntryStop(f),
      meterStop(f, {
        see: [['body.model', f.sub_model.id], ['x-cheese-child-model', f.sub_model.id], ['父会话跑的模型', '不是这一个']],
        say: [['它做的事', '把请求体里的模型名拿出来，放在请求头上交给准入']],
        note: '头里的模型名和父会话一样时是「继承」，不是指定——那种情况计量代理会把头删掉，按项目默认的分身模型走。',
      }),
      admissionStop(f, { allow: true, reason: f.budget.allow_reason, reason_kind: 'budget', pool: ASK.pool, model: f.sub_model.wire, key: true, note: `指定的模型在项目目录里、也在允许范围内，就用它：目录里的名字是 ${f.sub_model.id}，写进请求体的是 ${f.sub_model.wire}。翻译只发生在准入这一处。` }),
      gatewayStop(),
      vendorStop(f),
    ],
  },
  bare: {
    ok: (f) => [
      { ...sessionStop(f, 'bare'), head: '设备屏幕上的裸进程：发出一次 /v1/messages', act: '发起请求' },
      bareEntryStop(f),
      meterStop(f, { say: [['先做的事', `POST ${f.paths.admission}`], ['准入回答', 'allow: true，pool: gateway']], quota: Q('62%', '62%'), note: '从 CONNECT 进来的请求和从反向代理进来的一样：一个拦截点，一份改写。' }),
      admissionStop(f, { allow: true, reason: f.budget.allow_reason, reason_kind: 'budget', pool: ASK.pool, model: ASK.model, key: true, quota: Q('62%', '62%'), note: '项目从短期令牌里的声明认出来，不看请求头——请求头是会话自己的，伪造成本为零。' }),
      gatewayStop(),
      vendorStop(f),
    ],
    budget: (f) => [
      { ...sessionStop(f, 'bare'), head: '设备屏幕上的裸进程：发出一次 /v1/messages', act: '发起请求' },
      bareEntryStop(f),
      meterStop(f, { say: [['准入回答', 'allow: false，reason_kind: budget']], out: [['status', String(f.budget.status)], ['body', `{"type":"${f.budget.type}","message":"${f.budget.prefix}${f.budget.refusal_reason}"}`]], note: '这一份拒绝沿 CONNECT 隧道回到会话，形状和容器那条路完全一样。' }),
      admissionStop(f, { allow: false, reason: f.budget.refusal_reason, reason_kind: 'budget', pool: 'gateway（没给 key）', model: ASK.model, block: true, quota: Q('0%', '0%'), note: '额度用完：这一轮不执行，话题里出现一条平台提示，厂商那边一个请求都没到。' }),
    ],
    binding: (f) => [
      { ...sessionStop(f, 'bare'), head: '设备屏幕上的裸进程：发出一次 /v1/messages', act: '发起请求' },
      bareEntryStop(f),
      meterStop(f, { say: [['准入回答', 'allow: false，reason_kind: binding']], out: [['status', String(f.binding.status)], ['body', `{"type":"${f.binding.type}","message":"…能指定的模型：…"}`]], note: '拒绝的形状说的是真原因：卡上绑的模型目录里没有，重试多少次都一样。' }),
      admissionStop(f, { allow: false, reason: '这个项目不能跑 <模型名>；能指定的有：…', reason_kind: 'binding', pool: '（空）', model: '（没有）', block: true, note: '不会悄悄换到另一条路，厂商那边一个请求都没到。' }),
    ],
    failopen: (f) => [
      { ...sessionStop(f, 'bare'), head: '设备屏幕上的裸进程：发出一次 /v1/messages', act: '发起请求' },
      bareEntryStop(f),
      meterStop(f, { say: [['准入', '连不上主 API'], ['它自己的决定', '放行（fail-open）']], note: '放行不等于放任：订阅路自己的滚动 token 上限还在后面接着。' }),
      { ...admissionStop(f, { allow: true, reason: f.admission.fail_open_reason, reason_kind: 'budget', pool: 'subscription', model: '（没说，用客户端自己的）', soft: true, note: '判定是计量代理自己造的，它标了自己是 fail_open：这份回答里 pool 读出来是订阅路，但那只是一个默认值。' }), block: false },
      subscriptionStop(f),
      { ...vendorStop(f), note: '兜底的是订阅路的滚动 token 上限，不是准入。' },
    ],
    subagent: (f) => [
      { ...sessionStop(f, 'bare'), head: '设备屏幕上的分身进程：请求头带着它要的模型', act: '发起请求', see: [['authorization', `Bearer ${f.placeholder_token}`], ['body.model', f.sub_model.id], ['x-cheese-child-model', f.sub_model.id]] },
      bareEntryStop(f),
      meterStop(f, { see: [['body.model', f.sub_model.id], ['x-cheese-child-model', f.sub_model.id]], say: [['它做的事', '把模型名放在请求头上交给准入']], note: '和父会话一样的模型名是继承，不是指定。' }),
      admissionStop(f, { allow: true, reason: f.budget.allow_reason, reason_kind: 'budget', pool: ASK.pool, model: f.sub_model.wire, key: true, note: `允许范围内就用它：${f.sub_model.id} → ${f.sub_model.wire}，翻译只发生在准入这一处。` }),
      gatewayStop(),
      vendorStop(f),
    ],
  },
  cloud: {
    ok: (f) => [
      { ...sessionStop(f, 'cloud'), head: '云机器上的会话进程：调模型', act: '发起请求' },
      cloudHelperStop(f),
      cloudArriveStop(f),
      meterStop(f, { say: [['先做的事', `POST ${f.paths.admission}`], ['准入回答', 'allow: true，pool: gateway']], quota: Q('62%', '62%'), note: '改写和记账只有一份：隧道只是把包送进来。' }),
      admissionStop(f, { allow: true, reason: f.budget.allow_reason, reason_kind: 'budget', pool: ASK.pool, model: ASK.model, key: true, quota: Q('62%', '62%'), note: '机器上只有它自己的短期令牌，项目虚拟 key 一直在平台这一侧。' }),
      gatewayStop(),
      vendorStop(f),
    ],
    budget: (f) => [
      { ...sessionStop(f, 'cloud'), head: '云机器上的会话进程：调模型', act: '发起请求' },
      cloudHelperStop(f),
      cloudArriveStop(f),
      meterStop(f, { say: [['准入回答', 'allow: false，reason_kind: budget']], out: [['status', String(f.budget.status)], ['body', `{"type":"${f.budget.type}","message":"${f.budget.prefix}${f.budget.refusal_reason}"}`]], note: '拒绝沿隧道回到机器上，会话读到的是同一种错误。' }),
      admissionStop(f, { allow: false, reason: f.budget.refusal_reason, reason_kind: 'budget', pool: 'gateway（没给 key）', model: ASK.model, block: true, quota: Q('0%', '0%'), note: '额度用完：这一轮不执行，厂商那边一个请求都没到。' }),
    ],
    binding: (f) => [
      { ...sessionStop(f, 'cloud'), head: '云机器上的会话进程：调模型', act: '发起请求' },
      cloudHelperStop(f),
      cloudArriveStop(f),
      meterStop(f, { say: [['准入回答', 'allow: false，reason_kind: binding']], out: [['status', String(f.binding.status)], ['body', `{"type":"${f.binding.type}","message":"…能指定的模型：…"}`]], note: '绑定解析不出和额度无关，回的是「重试没用」那个形状。' }),
      admissionStop(f, { allow: false, reason: '这个项目不能跑 <模型名>；能指定的有：…', reason_kind: 'binding', pool: '（空）', model: '（没有）', block: true, note: '不会悄悄换到另一条路，厂商那边一个请求都没到。' }),
    ],
    failopen: (f) => [
      { ...sessionStop(f, 'cloud'), head: '云机器上的会话进程：调模型', act: '发起请求' },
      cloudHelperStop(f),
      cloudArriveStop(f),
      meterStop(f, { say: [['准入', '连不上主 API'], ['它自己的决定', '放行（fail-open）']], note: '隧道是常驻进程，但准入在主 API 上：主 API 重启的这几秒，模型流量不跟着断。' }),
      { ...admissionStop(f, { allow: true, reason: f.admission.fail_open_reason, reason_kind: 'budget', pool: 'subscription', model: '（没说，用客户端自己的）', soft: true, note: '判定是计量代理自己造的，pool 读出来是订阅路——那只是一个默认值。' }), block: false },
      subscriptionStop(f),
      { ...vendorStop(f), note: '兜底的是订阅路的滚动 token 上限。' },
    ],
  },
  codex: {
    ok: (f) => [
      sessionStop(f, 'codex'),
      { at: 'llmv1', head: `主 API 的 ${f.paths.catch_all}：这条路的主人是主 API 自己`, see: [['authorization', 'Bearer <会话的短期芝士令牌>'], ['请求的路径', `${f.paths.catch_all}/messages`]], out: [['authorization', 'Bearer <项目虚拟 key>'], ['x-api-key', '<项目虚拟 key>'], ['路径', '原样转发给网关']], say: [['没有的事', '不问准入、不做绑定解析、不改模型名']], note: 'Codex 和 Pi 引流不了，所以它们直接问平台。这条路只做两件事：认令牌、换 key，然后把上游的响应原样流回去。' },
      gatewayStop(),
      { ...vendorStop(f), note: '模型名是调用方自己写进请求体的：这条路没有准入替它决定。' },
    ],
    budget: (f) => [
      sessionStop(f, 'codex'),
      { at: 'llmv1', head: `主 API 的 ${f.paths.catch_all}：换上项目的虚拟 key`, see: [['authorization', 'Bearer <会话的短期芝士令牌>']], out: [['authorization', 'Bearer <项目虚拟 key>'], ['x-api-key', '<项目虚拟 key>']], note: '这条路每次请求都要项目的虚拟 key；要不到就拒，绝不退回用平台自己的凭据——那会把每个项目记到同一个桶里。' },
      { ...gatewayStop(), head: '网关路：额度这一步在这里', say: [['key 上的 max_budget', '跟着项目额度走'], ['超了就回', `${f.budget.status} ${f.budget.type}`]], block: true, quota: Q('0%', '0%'), note: '这条路上额度不是准入拦的：LiteLLM 自己按 key 上的 max_budget 拒绝，厂商那边一个请求都没到。同一个「额度用完」，两条入口的两个位置在拦：一条在准入，一条在网关。这是两条路最要紧的区别。', link: '/dev/llm#others' },
    ],
  },
}

// ---------- machines: one tool call out, and the model traffic back ----------
const MC_STATIONS = [
  { key: 'call', label: '会话里的工具调用', sub: '芝士调一个工具', col: 0, row: 2, tone: '--chart-1' },
  { key: 'result', label: '结果回到现场', sub: '对话里多一行', col: 0, row: 4, tone: '--chart-2' },
  { key: 'api', label: '主 API', sub: '发请求的那一侧', col: 1, row: 2, tone: '--chart-3' },
  { key: 'hub', label: '机器连接服务', sub: '一条长连接', col: 2, row: 2, tone: '--chart-5' },
  { key: 'screen', label: '屏幕里的 runner', sub: '骨架进程', col: 3, row: 2, tone: '--chart-4' },
  { key: 'exec', label: '执行器 / CLI', sub: '同一个目录', col: 4, row: 2, tone: '--chart-6' },
  { key: 'helper', label: '隧道助手', sub: 'HTTPS_PROXY 指向它', col: 3, row: 3, tone: '--chart-4' },
  { key: 'tunnel', label: '模型隧道', sub: 'WebSocket', col: 4, row: 3, tone: '--chart-5' },
  { key: 'meter', label: '计量代理', sub: '主机上', col: 5, row: 3, tone: '--chart-3' },
  { key: 'vendor', label: '模型厂商', sub: '', col: 5, row: 4, tone: '--faint' },
]

const MC_WIRES = [
  ['call', 'api'], ['api', 'hub'], ['hub', 'screen'], ['screen', 'exec'], ['exec', 'result'],
  ['screen', 'helper'], ['helper', 'tunnel'], ['tunnel', 'meter'], ['meter', 'vendor'],
  ['hub', 'result'], ['result', 'screen'],
]

const MC_BANDS = [
  { label: '机器主人那台机器：$HOME/.cheese', cells: [[3, 2], [4, 2]] },
  { label: '同一台机器', cells: [[3, 3]] },
]

const MC_ENTRIES = [
  { key: 'tool', label: '一次工具调用', sub: '从会话到机器再回来' },
]

const MC_SCENES = [
  { key: 'ok', label: '正常一轮', tone: '--ok' },
  { key: 'probe', label: '开跑前没应答', tone: '--warn' },
  { key: 'quarantine', label: '连续失败被隔离', tone: '--warn' },
  { key: 'unknown', label: '结果未知', tone: '--warn' },
]

const MC_MATRIX = { tool: ['ok', 'probe', 'quarantine', 'unknown'] }

const mcWalks = {
  tool: {
    ok: (f) => [
      { at: 'call', head: '芝士在会话里调一个工具', act: '工具调用', see: [['工具', 'Bash / Read / 写文件…'], ['会话在谁那里', '房间选的机器']], say: [['平台先做的事', '把这次调用记成一行（带 id 才记）']], note: `一次工具调用只有两种落点：返回一个结果，或者抛一个异常；中间那一种（发出去了、结果不会回来）由 ${f.dispatch_log} 记着。`, link: '/dev/machines#link' },
      { at: 'api', head: '主 API：找到这台机器的长连接', act: '工具调用', see: [['这次调用', '一个 id + 参数']], say: [['走哪条路', '机器连接服务的一条长连接']], note: '每个房间有机器，每个设备有一条长连接；调用不新开连接，是从已有的那条上过去。' },
      { at: 'hub', head: '机器连接服务：把请求交给屏幕的 runner', act: '工具调用', see: [['这条连接', '设备登录后一直开着']], say: [['常驻', '它单独跑，主 API 发版时不重启']], note: '所以发版一瞬，设备链接不断，机器上的会话也不用重连。', link: '/dev/topology#planes' },
      { at: 'screen', head: '屏幕里的 runner：骨架进程', act: '工具调用', see: [['这次调用', '一个请求体']], say: [['它做的事', '把请求交给执行器，在一个 socket 上等答复']], note: '屏幕是服务器在这个房间上开的一个终端，浏览器里看到的就是它的原始字节。' },
      { at: 'exec', head: '执行器 / CLI：在机器上执行', act: '工具调用', see: [['工作目录', `$HOME/${f.footprint_root}/…`]], say: [['它做的事', '真的跑这个工具，把输出收回来']], note: `平台装在这台机器上的所有东西——执行器、CLI、环境脚本、会话目录、包缓存——都在机器主人 $HOME 下的 ${f.footprint_root} 这一个目录里。`, link: '/dev/machines#footprint' },
      { at: 'result', head: '结果回到现场', act: '回到现场', say: [['回来的路', '同一条长连接（exec → 长连接 → 现场）'], ['落在哪', '对话里多一行工具结果']], note: '结果这一侧不怎么出错：真的出错了，才知道这次是「确定没做」还是「可能做过」。', link: '/dev/machines#failure' },
      { at: 'screen', head: '同一个会话进程，这次是调模型', act: '模型流量', see: [['HTTPS_PROXY', 'http://127.0.0.1:<隧道端口>']], say: [['它做的事', '把模型请求交给本机的隧道助手']], note: '机器上没有 root，改不了域名解析，只能靠 HTTPS_PROXY；它指向的是机器上那个回环口。', link: '/dev/machines#llm' },
      { at: 'helper', head: '隧道助手：把 CONNECT 装进一条 WebSocket', act: '模型流量', see: [['机器上有什么', '只有自己的短期芝士令牌']], say: [['它做的事', `把 CONNECT 发到平台的 ${f.paths.tunnel}`]], note: '凭证不上机器：能证明「记哪个项目的账」的只有那一个短期令牌。' },
      { at: 'tunnel', head: `模型隧道：${f.paths.tunnel}`, act: '模型流量', see: [['谁持有它', '单独一个进程，发版不影响']], say: [['它做的事', '把流量接到主机上的计量代理']], note: '隧道单独跑成一个进程，所以后端发版的那几秒，机器上的模型流量不会跟着断。' },
      { at: 'meter', head: '计量代理：从这里起和别处一样', act: '模型流量', say: [['接下来', '问准入、改写、按项目记账']], note: '机器来的流量和本机来的走同一个入口，账只有一份。详见模型调用流程。', link: '/dev/llm#one-exit' },
      { at: 'vendor', head: '模型厂商：token 流回来', act: '模型流量', say: [['返回', '和普通会话一样流式回来']], note: '机器在这一步只是路径的一端，账记在项目上。' },
    ],
    probe: (f) => [
      { at: 'call', head: '芝士在会话里调一个工具', act: '工具调用', see: [['会话在哪', '这台机器上还没有跑着的会话']], say: [['平台先做的事', '要在这台机器上重开会话，那就先探一下它答不答']], note: '探的是「这台机器现在能不能用」，不是「它上次好不好用过」。', link: '/dev/machines#failure' },
      { at: 'api', head: '主 API：问一句，等它答', act: '工具调用', say: [['最多等', `${f.probe_seconds} 秒`]], note: `一台租来的机器可能关机、可能刚重启：没应答就别往上启动会话。` },
      { at: 'hub', head: '机器连接服务：这 15 秒里没有答复', act: '工具调用', see: [['等的时间', `${f.probe_seconds} 秒`], ['结果', '没应答']], say: [['平台的决定', '不往这台机器上启动会话，这一轮不在这里跑']], block: true, note: '探测超时不由机器背账：它只是这一轮没赶上，重开一轮可以再探。', link: '/dev/machines#failure' },
    ],
    quarantine: (f) => [
      { at: 'call', head: '一次因为机器的失败', act: '工具调用', see: [['失败的原因', '归到机器头上，不归到话题']], say: [['记在哪', '记在这台设备上，不是话题上']], note: '同一台机器连续两次同类失败才隔离：一次是抖动，两次是它有问题。' },
      { at: 'api', head: '主 API：记账', act: '工具调用', say: [['第一次', '记一笔，继续']], note: `连续 ${f.failure_threshold} 次同类失败就进隔离，冷却 ${f.quarantine_minutes} 分钟。`, link: '/dev/machines#failure' },
      { at: 'hub', head: '长连接：又一轮落在同一台机器上', act: '工具调用', see: [['房间的选择', '还是这台机器']], say: [['第二次同类失败', '到这一步了']], note: '房间选了哪台就是哪台：话题不会自己悄悄换到别的机器上。' },
      { at: 'screen', head: '屏幕里的 runner：又是同一个原因', act: '工具调用', see: [['原因', '和上一次同一类']], say: [['累计', `连续 ${f.failure_threshold} 次`]], note: '判据是「连续 × 同类」，不是「总共多少次」。' },
      { at: 'exec', head: '执行器：这次没跑起来', act: '工具调用', say: [['结果', '这一轮失败']], note: '失败的原因是给机器用的，不是给话题用的。' },
      { at: 'screen', head: '结果沿原路回去', act: '回到现场', say: [['回到', '会话收到一个失败']] },
      { at: 'hub', head: '机器连接服务：把这台设备标上隔离', act: '工具调用', say: [['隔离多久', `${f.quarantine_minutes} 分钟`], ['期间', '调度会跳过这台机器']], note: '隔离的是设备，不是话题：别的房间也用不了它，但它没有坏掉——冷却是自愈的。' },
      { at: 'api', head: '房间收到一句话', act: '回到现场', say: [['房间里看到的', `「连续 ${f.failure_threshold} 轮因「…」失败」，点了是谁的机器`]], block: true, note: '话题留在原地，谁都没被搬到别处去：换机器会掩盖导致失败的那个原因。', link: '/dev/machines#failure' },
    ],
    unknown: (f) => [
      { at: 'call', head: '一个有副作用的工具调用', act: '工具调用', see: [['副作用', '写东西、发消息、改文件'], ['id', '带 id 的调用才记一行']], say: [['为什么要记', '崩溃恢复后要分得清「确定没做」和「可能做过」']], note: '幂等的问题不在执行器那侧：执行器的记录和它要回答的问题一起死在那台机器上。', link: '/dev/machines#failure' },
      { at: 'api', head: '主 API：先记一行', act: '工具调用', see: [['记下的', '发出去过什么，还没拿到结果']], say: [['这一行的含义', '行在 ⇒ 发出去过（不等于发生过）']], note: `只有带 id 的调用留一行：ping、context 这种问一遍和问两遍一样。`, link: '/dev/machines#failure' },
      { at: 'hub', head: '长连接：送出去', act: '工具调用', see: [['这次调用', '一个 id']], say: [['送出去了', '等结果']] },
      { at: 'screen', head: '屏幕里的 runner：交给执行器', act: '工具调用', say: [['执行器那一侧也记一行', '同一个 id，两边各记一次']], note: '两条记录说的是同一件事，只是其中一条活得比那台机器久。' },
      { at: 'exec', head: '执行器：跑着跑着，这台机器没了', act: '工具调用', say: [['结果', '永远回不来了'], ['平台这一行', '没有人写回来']], note: '这一行的 outcome 是空的，读出来就是 unknown。' },
      { at: 'screen', head: '回来的是「问不到了」', act: '回到现场', say: [['长连接', '断了，或者这一轮超时']] },
      { at: 'hub', head: '机器连接服务：不再等', act: '工具调用', say: [['平台的态度', '这一行交给人确认']], note: '「确定没做」才能重派；「可能做过」重派就是重发，副作用可能发生两次。' },
      { at: 'api', head: '结论：可能做过，交人确认', act: '回到现场', say: [['这一行', 'unknown'], ['房间里', '一条通知，请人去看那次改动落地没有']], block: true, note: 'unknown 写下去的意思不是「这次失败了」，是「平台不再问了」。', link: '/dev/machines#failure' },
    ],
  },
}

// ---------- assembling ----------
export const ARCH = {
  llm: { stations: LLM_STATIONS, wires: LLM_WIRES, entries: LLM_ENTRIES, scenes: LLM_SCENES, matrix: LLM_MATRIX, walks: llmWalks },
  machines: { stations: MC_STATIONS, wires: MC_WIRES, bands: MC_BANDS, entries: MC_ENTRIES, scenes: MC_SCENES, matrix: MC_MATRIX, walks: mcWalks },
}

const STATE_SEEN = new Set(['see', 'out', 'say'])

// Build the config one fence asks for, checking it against the topology as it
// goes: a station a walk names that the map does not have, a hop between two
// stations no wire connects, an entry or a scene the fence and this module
// disagree about — each one stops the build with the fence's line number.
export function archSpec(spec, where, missing, f) {
  const kind = String(spec.kind || '')
  if (!ARCH[kind]) missing(where, `«kind» is one of ${Object.keys(ARCH).join(', ')}, got «${kind || '（空）'}»`)
  const src = ARCH[kind]
  const stations = new Set(src.stations.map((s) => s.key))
  const wires = new Set(src.wires.map(([a, b]) => `${a}|${b}`))
  const wireOf = (a, b) => `${a}|${b}`
  for (const [a, b] of src.wires) {
    for (const k of [a, b]) if (!stations.has(k)) missing(where, `the map wires «${k}», which is not a station of ${kind}`)
  }

  const want = (list, what, have) => String(list || '').split(',').map((x) => x.trim()).filter(Boolean).map((x) => {
    if (!have.includes(x)) missing(where, `this fence names ${what} «${x}», the ${kind} figure has ${have.join(' · ')} — they change together`)
    return x
  })
  const entries = want(spec.entries, 'an entry', src.entries.map((e) => e.key))
  const scenes = want(spec.scenes, 'a scene', src.scenes.map((s) => s.key))
  if (!entries.length || !scenes.length) missing(where, 'a «demo-arch» needs «entries» and «scenes» — the two lists a reader picks from')

  const walks = {}
  for (const e of entries) {
    for (const s of scenes) {
      const fn = (src.walks[e] || {})[s]
      if (!fn) missing(where, `there is no walk for ${e} × ${s} — the fence may only offer pairs the figure has`)
      const stops = fn(f).map((st, i) => {
        const at = `${where}: ${e}/${s} stop ${i + 1}`
        if (!stations.has(st.at)) missing(at, `«${st.at}» is not a station of the ${kind} map`)
        if (!st.head) missing(at, 'a stop needs a «head» — the line the inspector opens with')
        for (const k of Object.keys(st)) if (!['at', 'head', 'note', 'link', 'act', 'block', 'soft', 'quota', 'see', 'out', 'say'].includes(k)) missing(at, `unknown field «${k}»`)
        for (const k of ['see', 'out', 'say']) if (st[k] && !Array.isArray(st[k])) missing(at, `«${k}» is a list of «key: value» pairs`)
        const q = st.quota
        if (q && (typeof q.from !== 'string' || typeof q.to !== 'string')) missing(at, '«quota» is {label, from, to} with two readings')
        if (st.link) st = { ...st, link: st.link }
        return st
      })
      for (let i = 0; i + 1 < stops.length; i++) {
        const a = stops[i].at, b = stops[i + 1].at
        if (!wires.has(wireOf(a, b)) && !wires.has(wireOf(b, a))) missing(where, `${e}/${s} hops ${a} → ${b}, and the map has no wire between them`)
      }
      if (!stops.some((s2) => s2.block) && !stops.some((s2) => s2.soft)) {
        // Not fatal: a walk that simply goes through is a legitimate telling.
      }
      walks[`${e}/${s}`] = { entry: e, scene: s, stops }
    }
  }

  // A fence that says which pairs it claims are refused is held to the walks:
  // if the figure stops refusing there, the prose and the picture have parted.
  for (const pair of String(spec.blocks || '').split(',').map((x) => x.trim()).filter(Boolean)) {
    const [e, s] = pair.split('/')
    const walk = walks[`${e}/${s}`]
    if (!walk) missing(where, `«blocks: ${pair}» names a pair this fence does not offer`)
    if (!walk.stops.some((x) => x.block)) missing(where, `this fence says ${pair} is refused, but the walk walks through — one of the two is wrong`)
  }

  const cfg = {
    kind,
    title: spec.title,
    note: spec.note || '',
    stations: src.stations,
    wires: src.wires,
    bands: src.bands || [],
    entries: src.entries.filter((e) => entries.includes(e.key)),
    scenes: src.scenes.filter((s) => scenes.includes(s.key)),
    matrix: Object.fromEntries(entries.map((e) => [e, (src.matrix[e] || []).filter((s) => scenes.includes(s))])),
    walks,
  }
  for (const e of entries) if (!cfg.matrix[e].length) missing(where, `entry «${e}» has no scene left after the fence's «scenes» list`)
  return cfg
}

// The same figure, said in prose: what a model reads instead of the map.
export function archText(cfg) {
  const label = (k) => (cfg.stations.find((s) => s.key === k) || { label: k }).label
  const lines = []
  for (const e of cfg.entries) {
    for (const s of cfg.matrix[e.key]) {
      const walk = cfg.walks[`${e.key}/${s}`]
      const scene = cfg.scenes.find((x) => x.key === s)
      const stops = walk.stops.map((st) => {
        const bits = [`${label(st.at)}：${st.head}`]
        for (const key of ['see', 'out', 'say']) if (st[key]) for (const [k, v] of st[key]) bits.push(`${key === 'see' ? '看到' : key === 'out' ? '送出' : '判定'} ${k}=${v}`)
        if (st.block) bits.push('拦在这里')
        if (st.soft) bits.push('软放行')
        return `  - ${bits.join('；')}`
      })
      const blocked = walk.stops.find((st) => st.block)
      lines.push(`- 入口「${e.label}」× 场景「${scene.label}」：${walk.stops.map((st) => label(st.at)).join(' → ')}${blocked ? `（在第 ${walk.stops.indexOf(blocked) + 1} 站 ${label(blocked.at)} 被拦下）` : '。'}\n${stops.join('\n')}`)
    }
  }
  return `**${cfg.title}**（网页上是一张可以点开每一站的路径图，这里是它的文字版：${cfg.entries.length} 个入口 × ${cfg.scenes.length} 个场景。）\n\n站名：${cfg.stations.map((s) => `${s.label}（${s.sub}）`).join(' · ')}。\n\n${lines.join('\n\n')}`
}
