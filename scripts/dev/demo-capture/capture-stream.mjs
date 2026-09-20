// Save the browser frame stream that agent-browser already serves, without sending input.
// The port comes from `agent-browser stream enable`; see README.md in this directory.
import { mkdirSync, writeFileSync, appendFileSync } from 'node:fs';
import { join } from 'node:path';
import { performance } from 'node:perf_hooks';

const [port, directory] = process.argv.slice(2);
if (!/^\d+$/.test(port ?? '') || !directory) throw new Error('Usage: capture-stream.mjs PORT NEW_DIRECTORY');
mkdirSync(directory, { recursive: true });
const log = join(directory, 'frames.jsonl');
const started = performance.now();
appendFileSync(log, JSON.stringify({event: 'start', utc: new Date().toISOString()}) + '\n');
let count = 0;
let closed = false;
const socket = new WebSocket(`ws://127.0.0.1:${port}/?maxFps=15`);
socket.addEventListener('open', () => console.log('Stream connected', new Date().toISOString()));
socket.addEventListener('message', event => {
  const message = JSON.parse(event.data);
  if (message.type !== 'frame' || !message.data) return;
  const elapsed = (performance.now() - started) / 1000;
  const file = `frame-${String(++count).padStart(6, '0')}.jpg`;
  writeFileSync(join(directory, file), Buffer.from(message.data, 'base64'));
  appendFileSync(log, JSON.stringify({event: 'frame', file, elapsed, metadata: message.metadata}) + '\n');
  if (count === 1 || count % 100 === 0) console.log('Frames', count, 'elapsed', elapsed.toFixed(2));
});
function finish(reason) {
  if (closed) return;
  closed = true;
  appendFileSync(log, JSON.stringify({event: 'stop', reason, frames: count, elapsed: (performance.now() - started) / 1000, utc: new Date().toISOString()}) + '\n');
  console.log('Stopped', reason, count);
  socket.close();
  clearTimeout(limit);
}
socket.addEventListener('error', () => { finish('stream-error'); process.exitCode = 1; });
socket.addEventListener('close', () => finish('stream-closed'));
process.on('SIGTERM', () => finish('requested'));
process.on('SIGINT', () => finish('requested'));
// A forgotten capture would fill the disk with frames, so it stops itself.
const limit = setTimeout(() => finish('20-minute-limit'), 20 * 60 * 1000);
