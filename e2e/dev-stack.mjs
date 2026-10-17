// `task dev:sandbox`: the whole cheese stack on loopback, without Docker, for a
// machine that has none (an agent's sandbox, a laptop without it). It starts
// the same servers e2e does (stack.mjs) with the dev server in front, prints the
// port to open, and stays in the foreground until it is stopped; stopping it
// stops every server it started. Postgres and Redis come from
// .claude/scripts/dev-db.sh and keep running after it, like a test run's.
//
// Nothing outside the machine is configured: no model provider (the stub
// gateway answers only "which models are there"), no forge, no mail, no
// object store. Sign in as alice / demo12345 (the seed migration's user).
//
//   node e2e/dev-stack.mjs        # or: task dev:sandbox
//
// Ports are stack.mjs's (E2E_*_PORT); the database is cheese_dev_sandbox on
// dev-db.sh's Postgres (CHEESEX_DEV_PG_PORT, CHEESEX_DEV_DB_DIR).
import { execFileSync, spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import net from 'node:net';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { BACKEND_PORT, COLLAB_PORT, FRONTEND_PORT, STUB_GATEWAY_PORT, stackServers } from './stack.mjs';

const E2E = dirname(fileURLToPath(import.meta.url));
const ROOT = dirname(E2E);
const BACKEND = join(ROOT, 'backend');
const FRONTEND = join(ROOT, 'frontend');
const DATABASE = 'cheese_dev_sandbox';
// Not db 0, which the backend suite uses on the same Redis: the login delay
// it counts per username would otherwise carry over between the two.
const REDIS_DB = 1;

const log = (line) => console.log(`[dev:sandbox] ${line}`);
function die(message) {
  console.error(`[dev:sandbox] ${message}`);
  process.exit(1);
}

// The backend reads backend/.env from its working directory, and a developer's
// copy configures the integrations this stack is meant to run without (model
// keys, a forge, mail).
if (existsSync(join(BACKEND, '.env'))) {
  die('backend/.env exists, and the backend would read the integrations it configures. Move it aside, or use `task dev`.');
}

// Only what the servers need from the caller's environment. Everything else —
// a model key, a proxy, the platform's own CHEESE_* variables — stays out, so
// nothing this stack runs can reach a real provider with someone's credentials.
const PASSED = /^(PATH|HOME|USER|LOGNAME|SHELL|LANG|LC_\w+|TERM|TMPDIR|TZ|XDG_\w+|UV_\w+|PNPM_\w+|COREPACK_\w+|npm_config_\w+|NODE_OPTIONS|SSL_CERT_\w+)$/;
const base = Object.fromEntries(Object.entries(process.env).filter(([key]) => PASSED.test(key)));

// Installing dependencies may need the machine's proxy; the servers do not, and
// a proxy in their environment would catch the backend's calls to the stub.
// dev-db.sh also takes its own CHEESEX_DEV_* settings.
const PROXY = /^(https?_proxy|all_proxy|no_proxy|CHEESEX_DEV_\w+)$/i;
const install = { ...base, ...Object.fromEntries(Object.entries(process.env).filter(([key]) => PROXY.test(key))) };

// A step's own output says why it failed; this only names the step.
function run(command, args, options = {}) {
  try {
    return execFileSync(command, args, { stdio: ['ignore', 'inherit', 'inherit'], env: base, ...options });
  } catch {
    die(`\`${command} ${args.join(' ')}\` failed (output above).`);
  }
}

for (const tool of ['uv', 'pnpm']) {
  try {
    // From frontend/, whose packageManager pins the pnpm a corepack shim runs.
    execFileSync(tool, ['--version'], { stdio: 'ignore', env: base, cwd: FRONTEND });
  } catch {
    die(`${tool} is not on PATH; this stack needs uv and pnpm (and node 22).`);
  }
}

function listening(port) {
  return new Promise((resolve) => {
    const socket = net.connect({ host: '127.0.0.1', port: Number(port) });
    socket.once('connect', () => (socket.destroy(), resolve(true)));
    socket.once('error', () => resolve(false));
  });
}

// A port that already answers belongs to someone else: another task's preview,
// or a stack started earlier. Starting beside it would show that one instead.
for (const [name, port] of Object.entries({ BACKEND_PORT, COLLAB_PORT, FRONTEND_PORT, STUB_GATEWAY_PORT })) {
  if (await listening(port)) die(`port ${port} is already in use; pick another with E2E_${name}=<port>.`);
}

const started = Date.now();
const seconds = () => `${((Date.now() - started) / 1000).toFixed(0)}s`;

log('postgres and redis (dev-db.sh servers)');
const exports = run('bash', [join(ROOT, '.claude/scripts/dev-db.sh'), 'servers'], {
  env: install,
  stdio: ['ignore', 'pipe', 'inherit'],
}).toString();
const db = Object.fromEntries([...exports.matchAll(/^export (\w+)=(.*)$/gm)].map(([, key, value]) => [key, value.replace(/^'|'$/g, '')]));
const env = {
  ...base,
  DATABASE_URL: `${db.TEST_PG_BASE}/${DATABASE}`,
  REDIS_URL: db.REDIS_URL.replace(/\/0$/, `/${REDIS_DB}`),
};
const pg = new URL(db.TEST_PG_BASE.replace('+asyncpg', ''));
const pgArgs = ['-h', pg.hostname, '-p', pg.port, '-U', pg.username];
const psql = (database, sql) =>
  run(join(db.PG_BIN, 'psql'), [...pgArgs, '-d', database, '-Atc', sql], { stdio: ['ignore', 'pipe', 'inherit'] }).toString().trim();
if (psql('postgres', `SELECT 1 FROM pg_database WHERE datname = '${DATABASE}'`) !== '1') {
  run(join(db.PG_BIN, 'createdb'), [...pgArgs, DATABASE]);
}

log(`dependencies (${seconds()})`);
run('uv', ['sync', '--frozen'], { cwd: BACKEND, env: install });
run('pnpm', ['install', '--frozen-lockfile'], { cwd: FRONTEND, env: install });
// Vite pre-bundles the dependencies the first time it starts. Inside the dev
// server, beside the other servers, that took the stack to 2.7 GB and was
// killed under a 2.5 GiB limit; as a step of its own, before any server runs,
// the peak is 1.3-1.8 GB, and the dev server then only reads the cache it
// left (docs/testing-without-docker.md has the measurements).
run('pnpm', ['exec', 'vite', 'optimize'], { cwd: FRONTEND, env: { ...base, VITE_API_BASE_URL: '/api' } });

log(`migrations (${seconds()})`);
run('uv', ['run', 'alembic', 'upgrade', 'head'], { cwd: BACKEND, env });
// alice comes from the migrations; her projects from the fusion demo seed, as
// on CI. Only while there are no projects at all: the seed replaces its own,
// and with them whatever was done in them since. Asked of the database rather
// than remembered, so a first run stopped before this point still seeds.
if (psql(DATABASE, 'SELECT count(*) FROM projects') === '0') {
  log(`seed (${seconds()})`);
  run('uv', ['run', 'python', '-m', 'scripts.seed_fusion_demo', '--without-repository'], { cwd: BACKEND, env });
}

const children = [];
function stop(code) {
  for (const child of children) {
    // Each server leads its own process group (detached), so this reaches the
    // shell, the package runner and the server under them alike.
    try {
      process.kill(-child.pid, 'SIGTERM');
    } catch {
      // already gone
    }
  }
  process.exit(code);
}
process.on('SIGINT', () => stop(130));
process.on('SIGTERM', () => stop(143));
// A closed terminal: the servers lead process groups of their own, so the
// hangup reaches only this process, and without this they would outlive it.
process.on('SIGHUP', () => stop(129));

async function answers(url, deadline) {
  while (Date.now() < deadline) {
    try {
      await fetch(url, { signal: AbortSignal.timeout(2000) });
      return true;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  }
  return false;
}

for (const server of stackServers({ ci: false, backendHost: '127.0.0.1', frontendHost: '127.0.0.1' })) {
  // The collaboration service listens on every interface unless told, and
  // takes Redis only to share documents between instances, of which there is
  // one here. Where Redis is dev-db.sh's fakeredis it cannot have it at all:
  // its client opens with INFO, which fakeredis lacks.
  const own = server.env.COLLAB_BACKEND_URL ? { COLLAB_ADDRESS: '127.0.0.1', REDIS_URL: '' } : {};
  const child = spawn(server.command, { cwd: E2E, shell: true, detached: true, stdio: 'inherit', env: { ...env, ...server.env, ...own } });
  children.push(child);
  child.once('exit', (code, signal) => {
    console.error(`[dev:sandbox] \`${server.command}\` exited (${signal ?? code}); stopping the rest.`);
    stop(1);
  });
  if (!(await answers(server.url, Date.now() + server.timeout))) {
    console.error(`[dev:sandbox] ${server.url} did not answer within ${server.timeout / 1000}s.`);
    stop(1);
  }
  log(`${server.url} is up (${seconds()})`);
}

log(`ready in ${seconds()}: frontend on 127.0.0.1:${FRONTEND_PORT}, sign in as alice / demo12345.`);
log(`FRONTEND_PORT=${FRONTEND_PORT}`);
