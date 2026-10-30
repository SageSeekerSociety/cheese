/**
 * With the interface set to English, the product's screens show no Chinese.
 *
 * The i18n gates (docs/i18n.md §5) read the catalog and the source: a key with
 * no English, a Chinese literal typed into `src/`. Neither renders anything, so
 * neither sees what reaches the screen from elsewhere — a sentence the backend
 * sends, a label built from a table, an attribute nobody looked at. This walks
 * the main screens as an English-speaking account and reads what is actually
 * rendered: every visible text node, and the `title`, `aria-label` and
 * `placeholder` of every visible element.
 *
 * What people wrote is not ours to translate. The seeded projects, teams and
 * challenges have Chinese names, and an English reader still sees them in
 * Chinese. Components that render such text mark it `data-user-content`:
 *
 *   - bare, on the element that shows it: the scan skips that element and
 *     everything inside it;
 *   - with a value, on an element whose attribute carries it (a project tile's
 *     `aria-label`): the value is the name, and only the name is excused.
 *
 * Either way the strings marked on a page are removed from every other text and
 * attribute on it before the check, so `Expand {team}` is still checked for the
 * `Expand`. The only other exemption is text that declares its own language
 * (`lang`), which is how a language switch names 中文. Nothing else is excused.
 *
 * An AI teammate's name is marked like a person's, but 芝士 never counts as
 * one: it is the name the platform gives a teammate nobody has renamed, stored
 * in Chinese for the agents that read it, and a screen shows it in its reader's
 * language (`teammateName` in frontend/src/lib/agentNames.ts).
 */
import { createHash } from "node:crypto";

import { expect, test, type Page } from "@playwright/test";
import { acceptPendingConsents, api, apiLogin, DEMO_PASSWORD } from "./helpers";

test.use({ locale: "en-US" });
// Parallel so CI shards split this file by test rather than handing one shard
// all of it: each test seeds what it needs, and one CI worker still runs them
// one at a time.
// 90 s: the workspace sweep, the longest test here, took 39.7 s on CI.
test.describe.configure({ mode: "parallel", timeout: 90_000 });

type Screen = {
  /** What a reader would call the screen; the failure names it. */
  name: string;
  path: string;
  /** Present once the screen has its content, not just its frame. */
  ready?: (page: Page) => Promise<void>;
  /** Steps a person takes after arriving (open a dialog, submit a form). */
  act?: (page: Page) => Promise<void>;
};

// Han ideographs (the definition catalog.spec.ts and the ratchet use), plus CJK
// and full-width punctuation: an English sentence has no 「」 or ，either.
const CJK_SOURCE =
  "[\\u3000-\\u303f\\u3400-\\u4dbf\\u4e00-\\u9fff\\uf900-\\ufaff\\uff01-\\uff60]";

// The stored name of a teammate nobody renamed: the platform's word, not a person's.
const DEFAULT_TEAMMATE = "芝士";

/** Every Chinese string on the page that no `data-user-content` region accounts for. */
async function chineseOnScreen(page: Page): Promise<string[]> {
  return page.evaluate(([source, platformName]) => {
    const cjk = new RegExp(source);
    // Marked or not, the default teammate name is checked like interface copy.
    const platformWords = (text: string) =>
      text.trim().replace(/^@/, "") === platformName;
    const ATTRIBUTES = ["title", "aria-label", "placeholder"];
    const REGION = '[data-user-content=""]';
    // A language names itself in every UI language (frontend/src/i18n/languages.ts);
    // the switch says 中文 to someone reading English on purpose.
    const OWN_LANGUAGE = '[lang]:not([lang|="en"])';
    const excused = (el: Element) => {
      if (el.closest(OWN_LANGUAGE)) return true;
      const region = el.closest(REGION);
      return !!region && !platformWords(region.textContent ?? "");
    };
    const shown = (el: Element) =>
      el.checkVisibility({ checkVisibilityCSS: true });

    // What people wrote, as it appears on this page: the text and attributes of
    // each marked region, and each named value. Longest first, so a name that
    // contains another is removed whole.
    const userStrings = new Set<string>();
    for (const el of document.querySelectorAll("[data-user-content]")) {
      const named = el.getAttribute("data-user-content")!.trim();
      if (named) {
        if (!platformWords(named)) userStrings.add(named);
        continue;
      }
      const text = (el.textContent ?? "").trim();
      if (text && !platformWords(text)) userStrings.add(text);
      for (const name of ATTRIBUTES) {
        const value = el.getAttribute(name)?.trim();
        if (value && !platformWords(value)) userStrings.add(value);
      }
    }
    const written = [...userStrings]
      .filter((s) => cjk.test(s))
      .sort((a, b) => b.length - a.length);
    const ours = (text: string) =>
      written.reduce((rest, s) => rest.split(s).join(""), text);

    const where = (el: Element) => {
      const parts: string[] = [];
      for (
        let e: Element | null = el;
        e && e !== document.body && parts.length < 4;
        e = e.parentElement
      ) {
        const classes = [...e.classList]
          .filter((c) => !c.startsWith("v-theme"))
          .slice(0, 2);
        parts.unshift(
          e.tagName.toLowerCase() + classes.map((c) => `.${c}`).join(""),
        );
      }
      return parts.join(" > ");
    };

    const found: string[] = [];
    const walker = document.createTreeWalker(
      document.body,
      NodeFilter.SHOW_TEXT,
    );
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const el = node.parentElement;
      const text = node.textContent ?? "";
      if (
        !el ||
        !cjk.test(text) ||
        ["SCRIPT", "STYLE", "NOSCRIPT"].includes(el.tagName)
      )
        continue;
      if (excused(el) || !shown(el) || !cjk.test(ours(text))) continue;
      found.push(`text         ${where(el)}  「${text.trim().slice(0, 120)}」`);
    }
    for (const el of document.body.querySelectorAll(
      ATTRIBUTES.map((a) => `[${a}]`).join(","),
    )) {
      if (excused(el) || !shown(el)) continue;
      for (const name of ATTRIBUTES) {
        const value = el.getAttribute(name) ?? "";
        if (cjk.test(ours(value)))
          found.push(
            `@${name.padEnd(11)} ${where(el)}  「${value.slice(0, 120)}」`,
          );
      }
    }
    return [...new Set(found)];
  }, [CJK_SOURCE, DEFAULT_TEAMMATE] as const);
}

/** Visit each screen and check it; every screen is reported, not just the first. */
async function check(page: Page, screens: Screen[]) {
  for (const screen of screens) {
    await test.step(screen.name, async () => {
      await page.goto(screen.path);
      if (screen.ready) await screen.ready(page);
      // Lists and counts arrive after the frame; a screen that polls never goes
      // idle, which is why this is bounded and not an assertion.
      await page
        .waitForLoadState("networkidle", { timeout: 10_000 })
        .catch(() => {});
      if (screen.act) await screen.act(page);
      // Read twice: what arrives late (a count, a toast) is caught by the second.
      const first = await chineseOnScreen(page);
      await page.waitForTimeout(500);
      const found = [...new Set([...first, ...(await chineseOnScreen(page))])];
      const report = `${screen.name} (${screen.path}) shows Chinese in English mode:\n  ${found.join("\n  ")}`;
      expect.soft(found, report).toEqual([]);
    });
  }
}

const visible = (selector: string) => async (page: Page) => {
  await page.locator(selector).first().waitFor({ timeout: 45_000 });
};

/** The data the workspace screens need, made once per worker through the API. */
type Seed = {
  projectId: string;
  roomId: string;
  taskId: string;
  feedbackId: string;
  othersProjectId: string;
  routineIds: string[];
};
let seeded: Seed | null = null;

/** The task's first commit, as its agent would push it. A card hands over a
 *  branch, and the platform refuses one with nothing on it. This writes straight
 *  to the run's isolated forge (backend/tests/forgejo, started by e2e.yml), which
 *  names a project's repository `cheese-<project id>/project`. */
async function pushToTaskBranch(page: Page, projectId: string, branch: string) {
  const forge = process.env.FORGEJO_API_URL;
  const token = process.env.FORGEJO_ADMIN_TOKEN;
  if (!forge || !token)
    throw new Error(
      "FORGEJO_API_URL / FORGEJO_ADMIN_TOKEN unset: this needs the isolated forge",
    );
  const repo = `cheese-${projectId.replace(/-/g, "")}/project`;
  const pushed = await page.request.post(
    `${forge}/repos/${repo}/contents/RELEASE_NOTES.md`,
    {
      headers: { Authorization: `token ${token}` },
      data: {
        content: Buffer.from("# Release notes\n").toString("base64"),
        message: "docs: add the release notes",
        new_branch: branch,
      },
    },
  );
  if (!pushed.ok())
    throw new Error(`forge commit → ${pushed.status()} ${await pushed.text()}`);
}

/** A project alice is not in, for the page a non-member is refused. Another
 *  demo account makes it; its token never reaches the browser. frank, because
 *  this accepts his pending consents and auth.spec.ts needs bobby, carol and
 *  david to still have theirs. */
async function othersProject(page: Page): Promise<string> {
  const signedIn = await page.request.post("/api/users/auth/login", {
    data: { username: "frank", password: DEMO_PASSWORD },
  });
  if (!signedIn.ok())
    throw new Error(
      `frank login → ${signedIn.status()} ${await signedIn.text()}`,
    );
  const token = (await signedIn.json()).data.accessToken as string;
  await acceptPendingConsents(page, token);
  const created = await page.request.post("/api/projects", {
    headers: { Authorization: `Bearer ${token}` },
    data: { name: "Private notes" },
  });
  if (!created.ok())
    throw new Error(
      `create project → ${created.status()} ${await created.text()}`,
    );
  return (await created.json()).data.id as string;
}

/** Use this worker's own room and a person-named teammate for the rules.
 * Do not rename the demo project's shared default teammate: other tests use it.
 * Default-name rendering on the routines page is a separate defect; this
 * fixture covers trigger translation without changing that copy or the scan. */
async function routineRoom(page: Page, projectId: string) {
  const agents = (await api(page, "get", `/projects/${projectId}/agents`)) as {
    data: { seat_handle: string; is_default: boolean }[];
  };
  const defaultSeat = agents.data.find((agent) => agent.is_default)!.seat_handle;
  const teammate = (await api(page, "post", `/projects/${projectId}/agents`, {
    display_name: "Nova",
  })) as { seat_handle: string };
  const room = (await api(page, "post", "/topics", {
    project_id: projectId,
    title: "Routine reports",
  })) as { id: string };
  await api(page, "post", `/topics/${room.id}/members`, { handle: teammate.seat_handle });
  const token = await page.evaluate(() => localStorage.getItem("accessToken"));
  const removed = await page.request.delete(`/api/topics/${room.id}/members/${defaultSeat}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!removed.ok())
    throw new Error(`remove default teammate → ${removed.status()} ${await removed.text()}`);
  return { roomId: room.id, agentSeat: teammate.seat_handle };
}

/** Create through the isolated backend's agent entrance — the path a teammate
 * takes, with the seat's own token. Rules run from the moment they are saved,
 * whoever saved them; nothing is left in the room for a person to confirm.
 * The signing secret follows the test backend's SANDBOX_TOKEN/JWT_SECRET env;
 * test-secret is the JWT_SECRET pinned by e2e.yml, not a deployment credential. */
async function agentRoutines(page: Page, roomId: string, agentSeat: string): Promise<string[]> {
  const agentToken = process.env.SANDBOX_TOKEN || createHash("sha256")
    .update(`cheesex:sandbox-signing-secret:v1:${process.env.JWT_SECRET || "test-secret"}`)
    .digest("hex");
  const ids: string[] = [];
  for (const rule of [
    {
      title: "Weekly progress",
      trigger: "schedule",
      spec: { freq: "weekly", weekdays: [0, 4], time: "09:00" },
    },
    {
      title: "Library review",
      trigger: "library_file_added",
      spec: { scope: "room" },
    },
  ]) {
    const response = await page.request.post(`/api/topics/${roomId}/routines`, {
      headers: { Authorization: `Bearer ${agentToken}` },
      data: {
        ...rule,
        instructions: "Summarize changes and save a report.",
        timezone: "Asia/Shanghai",
        owner_handle: "alice",
      },
    });
    if (!response.ok())
      throw new Error(`agent routine → ${response.status()} ${await response.text()}`);
    const routine = (await response.json()).data as {
      id: string; state: string; agent_handle: string; created_by: string;
    };
    expect(routine.state).toBe("active");
    expect(routine.agent_handle).toBe(agentSeat);
    expect(routine.created_by).toBe(agentSeat);
    ids.push(routine.id);
  }
  return ids;
}

async function seed(page: Page): Promise<Seed> {
  if (seeded) return seeded;
  const projects = (await api(page, "get", "/projects")) as {
    data: { id: string; owner_handle: string }[];
  };
  const projectId = projects.data.find((p) => p.owner_handle === "alice")!.id;
  // A room with a started task: making it starts it — alice makes it, so alice
  // reviews it — and leaves platform notices in the room. The task is what the
  // accept card is filed against.
  const room = (await api(page, "post", "/topics", {
    project_id: projectId,
    title: "Release checklist",
  })) as { id: string };
  const task = (await api(page, "post", `/topics/${room.id}/tasks`, {
    title: "Write the release notes",
  })) as { id: string; branch_name: string };
  await pushToTaskBranch(page, projectId, task.branch_name);
  await api(page, "post", `/topics/${task.id}/accept-card`, {
    reviewer_handle: "alice",
    change_subject: "docs: add the release notes",
  });
  // A library file goes in the way the page puts it in: take an upload, PUT
  // its one part (with its MD5) at the address handed out for it, and register
  // the upload with the bytes' SHA-256 and the part's MD5.
  const brief = Buffer.from("The brief.\n");
  const digest = createHash("md5").update(brief).digest("hex");
  const slot = (await api(page, "post", `/projects/${projectId}/library/uploads`, {
    name: "brief.txt",
    size: brief.length,
  })) as { upload: string };
  const part = (await api(page, "post", `/projects/${projectId}/library/uploads/part`, {
    upload: slot.upload,
    part: 1,
    md5: digest,
  })) as { url: string; headers: Record<string, string> };
  const put = await page.request.put(
    part.url.startsWith("/") ? `/api${part.url}` : part.url,
    { data: brief, headers: part.headers },
  );
  if (!put.ok())
    throw new Error(`library upload → ${put.status()} ${await put.text()}`);
  await api(page, "post", `/projects/${projectId}/library`, {
    upload: slot.upload,
    sha256: createHash("sha256").update(brief).digest("hex"),
    parts: [digest],
    filename: "brief.txt",
  });
  const feedback = (await api(page, "post", "/feedback", {
    kind: "bug",
    title: "The board forgets its filter",
    problem: "Switching projects resets the board filter.",
  })) as { id: string };
  const rules = await routineRoom(page, projectId);
  seeded = {
    projectId,
    roomId: room.id,
    taskId: task.id,
    feedbackId: feedback.id,
    othersProjectId: await othersProject(page),
    routineIds: await agentRoutines(page, rules.roomId, rules.agentSeat),
  };
  return seeded;
}

test.beforeEach(async ({ page }) => {
  await apiLogin(page, "en");
});

test("workspace: inbox, overview, tasks, room, accept card, library, project settings", async ({
  page,
}) => {
  const { projectId, roomId, taskId, routineIds } = await seed(page);
  const project = `/projects/${projectId}`;
  await check(page, [
    { name: "inbox", path: "/inbox" },
    {
      name: "project overview",
      path: `${project}/overview`,
      ready: visible('[data-testid="overview-people"]'),
    },
    {
      name: "project tasks",
      path: `${project}/tasks`,
      ready: visible('[data-testid="tasks-channel"]'),
    },
    {
      name: "room with platform notices",
      path: `${project}/topics/${roomId}`,
      // The card of the task its creator made on its own (「新建了任务」).
      ready: (page) =>
        page.getByTestId("task-created-post").first().waitFor({ timeout: 45_000 }),
    },
    {
      name: "room's members and environments",
      path: `${project}/topics/${roomId}`,
      ready: visible(".members-mini"),
      act: async (page) => {
        await page.locator(".members-mini").first().click();
        await page.locator(".env-action").first().click();
        await page.locator(".env-menu").waitFor();
      },
    },
    {
      name: "channel overview",
      path: `${project}/topics/${roomId}?tab=overview`,
      ready: visible("[data-testid=channel-overview]"),
    },
    // The task's card carries its accept card, open.
    {
      name: "task card with its accept card",
      path: `${project}/topics/${roomId}?card=${taskId}`,
      ready: visible(".accept-fold"),
    },
    {
      name: "library",
      path: `${project}/library`,
      ready: visible("text=brief.txt"),
    },
    { name: "project docs", path: `${project}/docs/charter` },
    {
      name: "routines with a clock and an event",
      path: `${project}/routines`,
      ready: async (page) => {
        for (const id of routineIds)
          await expect(page.locator(`[data-routine="${id}"]`)).toBeVisible();
      },
    },
    { name: "skills", path: `${project}/skills` },
    { name: "project members", path: `${project}/members` },
    { name: "member profile", path: `${project}/members/alice` },
    ...[
      "agents",
      "task-naming",
      "environment",
      "merge",
      "repository",
      "mcp",
      "archive",
    ].map((section) => ({
      name: `project settings: ${section}`,
      path: `${project}/settings/${section}`,
    })),
  ]);
});

test("account settings and devices", async ({ page }) => {
  await check(page, [
    { name: "profile", path: "/users/settings/profile" },
    { name: "password and security", path: "/users/settings/security" },
    { name: "connections", path: "/users/settings/connections" },
    { name: "public profile", path: "/users/alice" },
    { name: "archived projects", path: "/my/archived-projects" },
  ]);
});

test("teams: shared and personal", async ({ page }) => {
  await check(page, [
    { name: "my teams", path: "/teams/mine" },
    { name: "explore teams", path: "/teams/explore" },
    { name: "pending requests", path: "/teams/pending" },
    { name: "shared team", path: "/teams/team-1" },
    { name: "team members", path: "/teams/team-1/members" },
    { name: "team library", path: "/teams/team-1/library" },
    { name: "team compute", path: "/teams/team-1/compute" },
    { name: "team credits", path: "/teams/team-1/credits" },
    { name: "personal team", path: "/teams/alice" },
    { name: "personal team credits", path: "/teams/alice/credits" },
  ]);
});

test("spaces and challenges", async ({ page }) => {
  await check(page, [
    { name: "challenges in a space", path: "/spaces/1/tasks" },
    { name: "announcements", path: "/spaces/1/announcements" },
    { name: "challenge", path: "/spaces/1/tasks/1" },
    { name: "space members", path: "/spaces/1/manage/members" },
    { name: "space analytics", path: "/spaces/1/manage/analytics" },
    { name: "question", path: "/questions/1" },
  ]);
});

test("market and feedback", async ({ page }) => {
  const { feedbackId } = await seed(page);
  await check(page, [
    { name: "market", path: "/market" },
    { name: "feedback", path: "/feedback" },
    { name: "my feedback", path: "/feedback/mine" },
    { name: "new feedback", path: "/feedback/new" },
    { name: "a feedback report", path: `/feedback/${feedbackId}` },
  ]);
});

test("admin pages, as an admin", async ({ page }) => {
  // alice is on both admin allowlists (playwright.config.ts, backend env).
  await check(
    page,
    [
      "queue",
      "dashboard",
      "members",
      "models",
      "credits",
      "spaces",
      "integrations",
      "feature-stats",
      "ratchet",
    ].map((section) => ({
      name: `admin: ${section}`,
      path: `/admin/${section}`,
    })),
  );
});

test("download page in the browser, and the desktop app About dialog", async ({
  page,
}) => {
  await check(page, [{ name: "download", path: "/download" }]);

  // The page learns it runs in the app from what the app sets before the page
  // runs (frontend/src/lib/desktopApp.ts). This answers the few calls the About
  // dialog makes and nothing else.
  await page.addInitScript(() => {
    const w = window as unknown as Record<string, unknown>;
    w.__CHEESE_APP__ = {
      titleBar: "native",
      version: "0.1.42",
      can: ["updates", "links", "autostart"],
    };
    w.__TAURI__ = {
      core: {
        invoke: async (command: string) =>
          command === "update_status" || command === "check_for_updates"
            ? { state: "latest" }
            : null,
      },
      event: { listen: async () => () => {} },
    };
  });
  await check(page, [
    {
      name: "desktop About dialog",
      path: "/inbox",
      act: async (page) => {
        await page.locator(".help-entry").first().click();
        await page.getByText("About Cheese", { exact: false }).first().click();
        await page.locator(".about").waitFor();
      },
    },
    { name: "desktop general settings", path: "/users/settings/general" },
  ]);
});

test("error states: missing pages, a validation error, an API refusal", async ({
  page,
}) => {
  const { projectId, othersProjectId } = await seed(page);
  await check(page, [
    { name: "page not found", path: "/no-such-page" },
    {
      name: "project not found",
      path: "/projects/00000000-0000-4000-8000-000000000000",
    },
    {
      name: "project you are not in (403)",
      path: `/projects/${othersProjectId}`,
      ready: visible(".access-notice"),
    },
    {
      name: "feedback not found",
      path: "/feedback/00000000-0000-4000-8000-000000000000",
    },
    { name: "team not found", path: "/teams/no-such-team" },
    {
      name: "form validation error",
      path: "/users/settings/profile",
      act: async (page) => {
        await page.locator("#profile-intro").fill("x".repeat(80));
        await page.locator(".v-messages__message").first().waitFor();
      },
    },
    {
      name: "API refusal",
      path: `/projects/${projectId}/library`,
      act: async (page) => {
        // An empty file goes to the server, which refuses it (`emptyFile`).
        await page
          .locator('input[type="file"]')
          .first()
          .setInputFiles({
            name: "empty.txt",
            mimeType: "text/plain",
            buffer: Buffer.alloc(0),
          });
        await page.locator('.v-alert, [role="alert"]').first().waitFor();
      },
    },
  ]);
});
