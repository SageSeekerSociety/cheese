import { readFile } from 'node:fs/promises';
import { test, expect } from '@playwright/test';
import { apiLogin, openFirstProject } from './helpers';

// Emulate touch input and a narrow viewport, not an iOS/Android native album.
test.use({ hasTouch: true });

test('a video selected from the album uploads, persists and plays only on request', async ({ page, request }) => {
  await apiLogin(page);
  const rows = await openFirstProject(page);
  await rows.first().click();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator('.composer-input .ProseMirror').first()).toBeEnabled({ timeout: 15_000 });

  // A real, small VP8 recording exercises decode without a binary fixture or
  // depending on the codec of somebody's phone recording.
  const bytes = await page.evaluate(async () => {
    const canvas = document.createElement('canvas');
    canvas.width = 96;
    canvas.height = 54;
    const context = canvas.getContext('2d')!;
    const stream = canvas.captureStream(20);
    const recorder = new MediaRecorder(stream, { mimeType: 'video/webm;codecs=vp8' });
    const chunks: Blob[] = [];
    const finished = new Promise<void>((resolve, reject) => {
      recorder.ondataavailable = (event) => chunks.push(event.data);
      recorder.onstop = () => resolve();
      recorder.onerror = () => reject(new Error('Could not create video fixture'));
    });
    let frames = 0;
    recorder.start();
    const timer = setInterval(() => {
      context.fillStyle = frames % 2 ? '#2376ba' : '#e29637';
      context.fillRect(0, 0, canvas.width, canvas.height);
      if (++frames === 40) {
        clearInterval(timer);
        recorder.stop();
      }
    }, 50);
    try {
      await finished;
      return Array.from(new Uint8Array(await new Blob(chunks, { type: 'video/webm' }).arrayBuffer()));
    } finally {
      clearInterval(timer);
      stream.getTracks().forEach((track) => track.stop());
    }
  });
  expect(bytes.length).toBeGreaterThan(0);
  const name = `video-e2e-${Date.now()}.webm`;
  const album = page.locator('.composer-actions input[type="file"][accept]');
  await expect(album).toHaveAttribute('accept', 'image/*,video/*');
  // 照片或视频在「+」里：先打开它，再从底部面板里挑那一项。
  await page.getByLabel('更多操作', { exact: true }).click();
  const [chooser] = await Promise.all([
    page.waitForEvent('filechooser'),
    page.getByRole('menuitem', { name: '发送照片或视频' }).click(),
  ]);
  const upload = page.waitForResponse((response) =>
    new URL(response.url()).pathname.endsWith('/attachments') && response.request().method() === 'POST');
  await chooser.setFiles({ name, mimeType: 'video/webm', buffer: Buffer.from(bytes) });
  expect((await upload).ok()).toBe(true);
  await expect(page.locator('.composer-send')).toBeEnabled();
  await page.locator('.composer-send').click();

  const pane = page.getByTestId('chat-scroll');
  const row = pane.locator('.im-row', { hasText: name });
  await expect(row).toBeVisible();
  await expect(row).not.toHaveClass(/im-row--pending/);
  // Reload crosses the persisted message boundary, rather than playing a local
  // pending attachment that never reached the backend.
  await page.reload();
  await expect(row.getByRole('button', { name: '打开视频', exact: true })).toBeVisible();
  await expect(row.locator('video')).toHaveCount(0);

  const media = page.waitForResponse((response) =>
    new URL(response.url()).pathname.endsWith('/attachments/raw'));
  await row.getByRole('button', { name: '打开视频', exact: true }).click();
  const response = await media;
  expect(response.ok()).toBe(true);
  expect(response.headers()['content-type']).toContain('video/webm');
  // The independent request context has no login; the same file is not public.
  const unauthorized = await request.get(response.url());
  expect([401, 403]).toContain(unauthorized.status());

  const card = row.locator('.video-card');
  const player = card.locator('video');
  await expect(player).toBeVisible();
  await expect.poll(() => player.evaluate((video: HTMLVideoElement) => video.readyState)).toBeGreaterThanOrEqual(1);
  // 触屏：控制权在系统播放器手上（全屏、画中画、手势都得靠它），所以这里要的是
  // 原生控件，而不是自绘那一条。自绘那一条是精确指针的设备才摆的。
  expect(await player.evaluate((video: HTMLVideoElement) => ({
    paused: video.paused, autoplay: video.autoplay, controls: video.controls, inline: video.playsInline,
  }))).toEqual({ paused: true, autoplay: false, controls: true, inline: true });
  await expect(card.getByTestId('video-bar')).toHaveCount(0);
  const bounds = await player.boundingBox();
  expect(bounds).not.toBeNull();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);

  // 播放器自己的动作照旧：播到某一刻、停、拖到 0.05 秒。
  await player.evaluate((video: HTMLVideoElement) => video.play());
  await expect.poll(() => player.evaluate((video: HTMLVideoElement) => video.currentTime)).toBeGreaterThan(0.15);
  await player.evaluate((video: HTMLVideoElement) => video.pause());
  await expect.poll(() => player.evaluate((video: HTMLVideoElement) => video.paused)).toBe(true);
  await player.evaluate((video: HTMLVideoElement) => { video.currentTime = 0.05; });
  await expect.poll(() => player.evaluate((video: HTMLVideoElement) => video.seeking)).toBe(false);
  expect(await player.evaluate((video: HTMLVideoElement) => video.currentTime)).toBeCloseTo(0.05, 1);
});

for (const [suffix, mimeType] of [['avi', 'video/x-msvideo'], ['mkv', 'video/x-matroska']]) {
  test(`a download-only ${suffix} attachment does not offer an unusable player`, async ({ page }, testInfo) => {
    await apiLogin(page);
    const rows = await openFirstProject(page);
    await rows.first().click();
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.locator('.composer-input .ProseMirror').first()).toBeEnabled({ timeout: 15_000 });

    // Content is deliberately not a playable recording: this scenario checks
    // MIME-based classification and byte-for-byte download, not codec support.
    const content = Buffer.from(`download-only ${suffix} fixture`);
    const name = `video-download-e2e-${Date.now()}.${suffix}`;
    await page.getByLabel('更多操作', { exact: true }).click();
    const [chooser] = await Promise.all([
      page.waitForEvent('filechooser'),
      page.getByRole('menuitem', { name: '发送照片或视频' }).click(),
    ]);
    const upload = page.waitForResponse((response) =>
      new URL(response.url()).pathname.endsWith('/attachments') && response.request().method() === 'POST');
    await chooser.setFiles({ name, mimeType, buffer: content });
    const response = await upload;
    expect(response.ok()).toBe(true);
    expect((await response.json()).data.mime).toBe(mimeType);
    await expect(page.locator('.composer-send')).toBeEnabled();
    await page.locator('.composer-send').click();

    const row = page.getByTestId('chat-scroll').locator('.im-row', { hasText: name });
    await expect(row).toBeVisible();
    await expect(row).not.toHaveClass(/im-row--pending/);
    await page.reload();
    const file = row.locator('.im-file-link');
    await expect(file).toBeVisible();
    await expect(row.locator('.video-card, video')).toHaveCount(0);
    const [download] = await Promise.all([page.waitForEvent('download'), file.click()]);
    expect(download.suggestedFilename()).toBe(name);
    const savedPath = testInfo.outputPath(`download-${suffix}`);
    await download.saveAs(savedPath);
    expect(await readFile(savedPath)).toEqual(content);
  });
}
