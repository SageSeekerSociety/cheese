// 后端启动时要有一份模型目录：一条活绑哪个模型，选项来自网关（/model/info），
// 没有网关的部署就只剩它配置里的那一个。这里只答这一个问题。
//
// This is not an inference provider and does not become one: it answers the
// admin question "what do you route", and 404s everything else, which the
// backend treats the same way it treats a gateway it cannot reach (every admin
// call there is best-effort by construction). Turns still have nothing to run
// on, which is the point of the e2e environment.
import { createServer } from 'node:http';

const PORT = Number(process.env.STUB_GATEWAY_PORT || 4010);

const priced = { input_cost_per_token: 0.000004, output_cost_per_token: 0.000004 };

const MODELS = [
  {
    model_name: 'glm-5.2',
    litellm_params: { model: 'anthropic/glm-5.2', ...priced },
    model_info: { db_model: false, cheese_selectable: true, cheese_label: 'GLM-5.2' },
  },
  {
    model_name: 'deepseek-flash',
    litellm_params: { model: 'deepseek/deepseek-flash' },
    model_info: {
      db_model: false,
      cheese_selectable: true,
      cheese_label: 'DeepSeek V4.1 Flash',
      ...priced,
    },
  },
  // Routed, never a menu item — the same shape the deployed gateway gives the
  // model the subagent alias points at.
  {
    model_name: 'glm-4.5',
    litellm_params: { model: 'anthropic/glm-4.5', ...priced },
    model_info: { db_model: false },
  },
];

createServer((request, response) => {
  const path = (request.url || '').split('?')[0];
  if (path === '/model/info' || path === '/v1/model/info') {
    response.writeHead(200, { 'content-type': 'application/json' });
    response.end(JSON.stringify({ data: MODELS }));
    return;
  }
  if (path === '/healthz') {
    response.writeHead(200, { 'content-type': 'text/plain' });
    response.end('ok');
    return;
  }
  response.writeHead(404, { 'content-type': 'application/json' });
  response.end(JSON.stringify({ error: 'stub gateway answers /model/info only' }));
}).listen(PORT, '127.0.0.1', () => {
  console.log(`stub gateway on :${PORT}`);
});
