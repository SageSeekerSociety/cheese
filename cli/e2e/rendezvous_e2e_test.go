//go:build claudee2e

// Prompt delivery over the rendezvous socket, against a REAL Claude Code.
//
// Delivery goes through the socket Claude Code binds for itself, and this
// asserts the property that made us switch: it does not depend on anything the
// terminal does. Every test here runs the pane at 46 columns — the exact width
// that made the screen-scraping driver re-paste a CJK prompt forever on
// 2026-08-16 — and sends Chinese text through it.
//
// What is proven, not assumed: the file the scripted tool writes. A prompt that
// reaches the composer but is never submitted leaves a pane that looks perfect
// and a file that never grows.
package e2e

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"image"
	"image/color"
	"image/png"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/SageSeekerSociety/cheese/cli/internal/rendezvous"
	"github.com/SageSeekerSociety/cheese/cli/internal/terminal"
)

// paneCols is deliberately hostile: 46 columns is where a 24-CJK-character
// anchor (48 display columns) can never fit on one row.
const paneCols = 46
const paneRows = 30

// realTempDir is t.TempDir() with symlinks resolved.
//
// On macOS the temp root is /var/..., a symlink to /private/var/..., and Claude
// Code keys its per-project trust flag on the RESOLVED cwd. Writing the gate
// under the unresolved path leaves the "Do you trust this folder?" dialog up,
// which looks exactly like a driver that failed to deliver anything — the
// readiness wait just times out with a menu on screen. (Linux CI never sees it,
// which is why it went unnoticed.)
func realTempDir(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	resolved, err := filepath.EvalSymlinks(dir)
	if err != nil {
		return dir
	}
	return resolved
}

// claudeBinary is the build under test: the PINNED one when the device has it,
// so e2e exercises what production launches, overridable for a bisect.
func claudeBinary(t *testing.T) string {
	t.Helper()
	if p := os.Getenv("CHEESE_E2E_CLAUDE"); p != "" {
		return p
	}
	pinned := filepath.Join(os.Getenv("HOME"), ".local", "share", "claude", "versions", pinnedVersion)
	if fi, err := os.Stat(pinned); err == nil && fi.Mode()&0o111 != 0 {
		return pinned
	}
	p, err := exec.LookPath("claude")
	if err != nil {
		t.Skip("no `claude` on PATH")
	}
	return p
}

// Must track device_launch.CLAUDE_PINNED_VERSION.
const pinnedVersion = "2.1.261"

type rvFixture struct {
	client    *rendezvous.Client
	sess      *terminal.Session
	markFile  string
	mark      string
	configDir string
	apiBase   string
	sock      string
	token     string
	pane      func() string

	framesMu sync.Mutex
	frames   []string // types of every frame the session pushed
}

func (f *rvFixture) frameCount(kind string) int {
	f.framesMu.Lock()
	defer f.framesMu.Unlock()
	n := 0
	for _, t := range f.frames {
		if t == kind {
			n++
		}
	}
	return n
}

// userTurns counts how many USER messages in the session transcript contain
// needle. This is the direct evidence for "delivered exactly once": the mock
// never echoes a prompt back, so a marker appearing twice means the prompt was
// delivered twice, and zero means it never became a turn at all. Counting tool
// runs cannot answer this — one prompt can legitimately run a tool more than
// once.
// userTurns counts how many times a prompt became a turn the MODEL saw.
//
// The evidence is the mock's recorded requests, not Claude Code's transcript
// files and not the screen. The last /v1/messages request carries the whole
// conversation, so the number of times a marker appears in it is the number of
// user turns that prompt produced: 0 = never delivered, 2 = delivered twice.
// The scripted replies ("Doing it." / "done") never echo a marker, so nothing
// else can inflate the count.
//
// This replaced a transcript-shape judgement that reported zero for a prompt
// visibly on screen and already answered (CI, 2026-08-17) — a check that can
// disagree with reality is worse than no check, because it sends you to debug
// the wrong layer.
func (f *rvFixture) userTurns(needle string) int {
	body, err := f.lastModelRequest()
	if err != nil {
		return 0
	}
	return strings.Count(body, needle)
}

// lastModelRequest returns the body of the most recent /v1/messages the mock
// received, as raw JSON text.
func (f *rvFixture) lastModelRequest() (string, error) {
	req, err := http.NewRequest(http.MethodPut,
		f.apiBase+"/mockserver/retrieve?type=requests&format=json", nil)
	if err != nil {
		return "", err
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	var buf bytes.Buffer
	if _, err := buf.ReadFrom(resp.Body); err != nil {
		return "", err
	}
	var recorded []struct {
		Path string          `json:"path"`
		Body json.RawMessage `json:"body"`
	}
	if err := json.Unmarshal(buf.Bytes(), &recorded); err != nil {
		return "", fmt.Errorf("decode recorded requests: %w", err)
	}
	last := ""
	for _, r := range recorded {
		if strings.Contains(r.Path, "/v1/messages") {
			last = string(r.Body)
		}
	}
	return last, nil
}

// transcriptHits is kept for diagnostics only — see dumpTranscript.
func (f *rvFixture) transcriptHits(needle string) (int, []string) {
	paths, _ := filepath.Glob(filepath.Join(f.configDir, "projects", "*", "*.jsonl"))
	n := 0
	var mentions []string
	for _, p := range paths {
		b, err := os.ReadFile(p)
		if err != nil {
			continue
		}
		for _, line := range strings.Split(string(b), "\n") {
			if !strings.Contains(line, needle) {
				continue
			}
			if len(line) > 400 {
				line = line[:400] + "…"
			}
			mentions = append(mentions, line)
			if strings.Contains(line, `"role":"user"`) {
				n++
			}
		}
	}
	return n, mentions
}

// dumpTranscript reports what the transcript actually holds for a marker, and
// how many .jsonl files were even found.
func (f *rvFixture) dumpTranscript(t *testing.T, needle string) {
	t.Helper()
	paths, _ := filepath.Glob(filepath.Join(f.configDir, "projects", "*", "*.jsonl"))
	n, mentions := f.transcriptHits(needle)
	t.Logf("transcript: %d file(s) under %s; %q → %d user-role hits, %d mentions",
		len(paths), filepath.Join(f.configDir, "projects"), needle, n, len(mentions))
	if body, err := f.lastModelRequest(); err == nil {
		t.Logf("model saw %q %d time(s) in the last request (%d bytes)",
			needle, strings.Count(body, needle), len(body))
	} else {
		t.Logf("could not read the mock's recorded requests: %v", err)
	}
	for i, m := range mentions {
		t.Logf("  mention[%d]: %s", i, m)
	}
	if len(mentions) == 0 {
		for _, p := range paths {
			b, err := os.ReadFile(p)
			if err != nil {
				continue
			}
			lines := strings.Split(strings.TrimSpace(string(b)), "\n")
			t.Logf("  %s: %d lines; last line: %.400s", filepath.Base(p), len(lines), lines[len(lines)-1])
		}
	}
}

// startRendezvousClaude boots a real Claude Code with the three env vars that
// make it bind a rendezvous socket, waits for the input box, and dials.
func startRendezvousClaude(t *testing.T) *rvFixture {
	t.Helper()
	claudeBin := claudeBinary(t)

	work := realTempDir(t)
	markFile := filepath.Join(work, "heard.txt")
	mark := fmt.Sprintf("claude-acted-%d", time.Now().UnixNano())
	// The shared helper scripts exactly two tool calls and answers everything
	// after that with end_turn. That budget is deliberate and NOT raised here: an
	// unlimited tool expectation answers each tool RESULT with another tool call,
	// so a single prompt loops forever (measured: 148 runs for 8 prompts). Tests
	// that need per-prompt accounting read the transcript instead — see
	// userTurns.
	apiBase := startMockAPI(t, markFile, mark)

	home := realTempDir(t)
	// Claude Code can finish the prompt while a short-lived plugin staging task
	// is still unwinding. Session cleanup runs before this callback (LIFO); retry
	// the exact test home so TempDir's own cleanup cannot lose a race with that
	// exiting child and turn a passed transport assertion into directory-not-empty.
	t.Cleanup(func() {
		deadline := time.Now().Add(5 * time.Second)
		for {
			if err := os.RemoveAll(home); err == nil || time.Now().After(deadline) {
				return
			}
			time.Sleep(100 * time.Millisecond)
		}
	})
	configDir := filepath.Join(home, ".claude")
	if err := os.MkdirAll(configDir, 0o755); err != nil {
		t.Fatal(err)
	}
	gates := fmt.Sprintf(`{"hasCompletedOnboarding":true,"autoUpdates":false,`+
		`"bypassPermissionsModeAccepted":true,"projects":{%q:`+
		`{"hasTrustDialogAccepted":true,"hasCompletedProjectOnboarding":true}}}`, work)
	if err := os.WriteFile(filepath.Join(configDir, ".claude.json"), []byte(gates), 0o644); err != nil {
		t.Fatal(err)
	}

	// A short socket dir: sun_path caps at ~104 bytes and t.TempDir() on macOS
	// already spends most of that under /var/folders.
	sockDir, err := os.MkdirTemp("", "rv")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(sockDir) })
	sock := filepath.Join(sockDir, "s.sock")
	const token = "e2e-rendezvous-token"

	isolateTmux(t)
	m, err := terminal.NewManager()
	if err != nil {
		t.Fatalf("NewManager: %v", err)
	}
	t.Cleanup(func() { m.KillServer() })

	sess, err := m.Spawn("rv-e2e",
		[]string{"bash", "-lc", "cd " + work + " && exec " + claudeBin + " --dangerously-skip-permissions"},
		[]string{
			"HOME=" + home,
			"CLAUDE_CONFIG_DIR=" + configDir,
			"ANTHROPIC_BASE_URL=" + apiBase,
			"ANTHROPIC_AUTH_TOKEN=mock",
			"ANTHROPIC_MODEL=claude-sonnet-4-5",
			"DISABLE_AUTOUPDATER=1",
			"NO_PROXY=127.0.0.1,localhost",
			"no_proxy=127.0.0.1,localhost",
			// The trio. Without the first, no socket is ever bound — which is
			// itself asserted by TestRendezvousAbsentWithoutTheBackendSwitch.
			"CLAUDE_BG_BACKEND=daemon",
			"CLAUDE_BG_RENDEZVOUS_SOCK=" + sock,
			"CLAUDE_BG_RV_AUTH=" + token,
		}, paneCols, paneRows)
	if err != nil {
		t.Fatalf("Spawn: %v", err)
	}
	t.Cleanup(func() { sess.Close() })

	// Snapshot() serves whatever the poller last captured, and the poller only
	// starts on the first OnChange registration. Nothing in production registers
	// one — nothing there reads a screen — so this test has to, or every
	// snapshot is the empty string and the readiness wait below can only ever
	// time out. Purely so the TEST can watch claude boot.
	sess.OnChange(func() {})

	pane := func() string { return sess.Snapshot() }
	dumpPane := func() { t.Logf("--- pane (%d cols) ---\n%s", paneCols, pane()) }

	waitFor(t, "Claude Code to reach a working prompt", 150*time.Second, func() bool {
		s := pane()
		return strings.Contains(s, "❯") &&
			!strings.Contains(s, "trust this folder") &&
			!strings.Contains(s, "bypass permissions?")
	}, dumpPane)

	f := &rvFixture{sess: sess, markFile: markFile, mark: mark,
		configDir: configDir, apiBase: apiBase, sock: sock, token: token, pane: pane}

	c, err := rendezvous.Dial(context.Background(), sock, token, rendezvous.Options{
		WaitForSocket: 60 * time.Second,
		Logf:          func(format string, a ...any) { t.Logf("rendezvous: "+format, a...) },
		OnFrame: func(fr rendezvous.Frame) {
			f.framesMu.Lock()
			f.frames = append(f.frames, fr.Type)
			f.framesMu.Unlock()
		},
	})
	if err != nil {
		dumpPane()
		t.Fatalf("dial rendezvous socket: %v", err)
	}
	t.Cleanup(func() { c.Close() })
	f.client = c

	return f
}

func (f *rvFixture) marks() int {
	b, err := os.ReadFile(f.markFile)
	if err != nil {
		return 0
	}
	return strings.Count(string(b), f.mark)
}

// The headline property: a CJK prompt in a 46-column pane — the exact shape that
// wedged production — is delivered and ACTED on.
func TestRendezvousDeliversCJKInANarrowPane(t *testing.T) {
	f := startRendezvousClaude(t)
	prompt := "【平台】以下是平台自动发出的指令，不是任何人手打的话：请运行那个工具。"
	if err := f.client.Reply(prompt); err != nil {
		t.Fatalf("reply: %v", err)
	}
	waitFor(t, "the CJK prompt to be acted on", 150*time.Second,
		func() bool { return f.marks() >= 1 },
		func() { t.Logf("--- pane ---\n%s", f.pane()) })
}

// A rendezvous reply can only carry text.  Image parity therefore depends on
// Claude Code resolving an @-mentioned local image through its own native
// prompt attachment path — the same model-facing image block produced by a
// clipboard paste — rather than on the model deciding to call Read later.
// Assert the actual /v1/messages body: a path rendered in the pane is not proof
// that the image bytes reached the model.
func TestRendezvousImagePathBecomesNativeImageBlock(t *testing.T) {
	f := startRendezvousClaude(t)

	img := image.NewRGBA(image.Rect(0, 0, 2, 1))
	img.Set(0, 0, color.RGBA{R: 255, A: 255})
	img.Set(1, 0, color.RGBA{B: 255, A: 255})
	var encoded bytes.Buffer
	if err := png.Encode(&encoded, img); err != nil {
		t.Fatalf("encode test image: %v", err)
	}
	imagePath := filepath.Join(filepath.Dir(f.markFile), "rendezvous-image.png")
	if err := os.WriteFile(imagePath, encoded.Bytes(), 0o644); err != nil {
		t.Fatalf("write test image: %v", err)
	}

	const marker = "RVIMAGE-001"
	prompt := marker + " Describe the image attached from @rendezvous-image.png"
	if err := f.client.Reply(prompt); err != nil {
		t.Fatalf("reply with image path: %v", err)
	}
	waitFor(t, "the image prompt to reach the model", 150*time.Second, func() bool {
		body, err := f.lastModelRequest()
		return err == nil && strings.Contains(body, marker)
	}, func() {
		t.Logf("--- pane ---\n%s", f.pane())
		f.dumpTranscript(t, marker)
	})

	body, err := f.lastModelRequest()
	if err != nil {
		t.Fatalf("read recorded model request: %v", err)
	}
	wantBase64 := base64.StdEncoding.EncodeToString(encoded.Bytes())
	var request struct {
		JSON struct {
			Messages []struct {
				Content any `json:"content"`
			} `json:"messages"`
		} `json:"json"`
	}
	if err := json.Unmarshal([]byte(body), &request); err != nil {
		t.Fatalf("decode recorded model request: %v; request: %.2000s", err, body)
	}
	found := false
	for _, message := range request.JSON.Messages {
		blocks, ok := message.Content.([]any)
		if !ok {
			continue
		}
		for _, rawBlock := range blocks {
			block, ok := rawBlock.(map[string]any)
			if !ok || block["type"] != "image" {
				continue
			}
			source, ok := block["source"].(map[string]any)
			if ok && source["media_type"] == "image/png" && source["data"] == wantBase64 {
				found = true
			}
		}
	}
	if !found {
		t.Fatalf("@-mentioned image did not become a native image block; request: %.2000s", body)
	}
	waitFor(t, "the image prompt to finish", 150*time.Second,
		func() bool { return f.marks() >= 1 },
		func() { t.Logf("--- pane ---\n%s", f.pane()) })
}

// tmux send-keys had to chunk anything long, and a chunk boundary inside a
// multi-byte rune produced two invalid arguments and a silently lost message.
func TestRendezvousDeliversA30KBPrompt(t *testing.T) {
	f := startRendezvousClaude(t)
	long := "请运行那个工具。" + strings.Repeat("这是一条用来撑过 tmux 单条命令上限的长消息。", 900)
	if err := f.client.Reply(long); err != nil {
		t.Fatalf("reply: %v", err)
	}
	waitFor(t, "the 30KB prompt to be acted on", 240*time.Second,
		func() bool { return f.marks() >= 1 },
		func() { t.Logf("--- pane ---\n%s", f.pane()) })
}

// The turn-opener production shape: one prompt per turn, the next only after the
// previous one became a turn. Every prompt must arrive, exactly once — a re-send
// that the old screen-scraping driver would have made (its verification could
// never succeed) shows up here as a second copy.
//
// Deliberately NOT a back-to-back burst. That measures how a busy session
// handles frames landing on top of it — a real limit, documented on Reply and
// covered by the platform's hook-receipt fallback for mid-turn supplements, but
// not this transport's contract and therefore not the property of this test.
func TestRendezvousDeliversEveryPromptInATurnBasedSequence(t *testing.T) {
	const n = 6
	f := startRendezvousClaude(t)
	markers := make([]string, n)
	for i := range n {
		markers[i] = fmt.Sprintf("RVSEQ-%03d", i+1)
		p := fmt.Sprintf("%s 第 %d 条：请运行那个工具。这条消息包含中文与换行\n"+
			"以及一段较长的正文，用来逼近软换行的边界。", markers[i], i+1)
		if err := f.client.Reply(p); err != nil {
			t.Fatalf("reply %d: %v", i+1, err)
		}
		mk := markers[i]
		waitFor(t, fmt.Sprintf("prompt %d to become a user turn", i+1), 120*time.Second,
			func() bool { return f.userTurns(mk) > 0 },
			func() {
				t.Logf("--- pane ---\n%s", f.pane())
				f.dumpTranscript(t, mk)
			})
	}

	time.Sleep(3 * time.Second) // let a stray duplicate show up before we judge
	for i, mk := range markers {
		if got := f.userTurns(mk); got != 1 {
			t.Fatalf("prompt %d (%s) became %d user turns, want exactly 1", i+1, mk, got)
		}
	}
}

// Concurrent senders must not corrupt the wire. Interleaved frames would make
// the session drop BOTH halves, so a well-formed delivery is the property under
// test here — not that a busy session turns each one into a turn, which it does
// not promise (see the contract on Reply; the platform's hook receipt is what
// covers that, one layer up).
func TestRendezvousHandlesConcurrentSendersWithoutCorruption(t *testing.T) {
	const n = 5
	f := startRendezvousClaude(t)
	markers := make([]string, n)
	var wg sync.WaitGroup
	errs := make(chan error, n)
	for i := range n {
		markers[i] = fmt.Sprintf("RVCONC-%03d", i+1)
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			if err := f.client.Reply(fmt.Sprintf("%s 并发第 %d 条：请运行那个工具。",
				markers[i], i+1)); err != nil {
				errs <- err
			}
		}(i)
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		t.Fatalf("concurrent reply reported a failure: %v", err)
	}

	// At least one must land — zero would mean the connection itself broke.
	delivered := func() int {
		got := 0
		for _, mk := range markers {
			if f.userTurns(mk) > 0 {
				got++
			}
		}
		return got
	}
	waitFor(t, "at least one concurrent prompt to become a user turn", 120*time.Second,
		func() bool { return delivered() > 0 },
		func() { t.Logf("--- pane ---\n%s", f.pane()) })

	// Whatever landed must have landed ONCE. A duplicate would mean a frame was
	// somehow processed twice — a far worse failure than one that was dropped,
	// because the room would see the same message from the person twice.
	time.Sleep(3 * time.Second)
	for _, mk := range markers {
		if got := f.userTurns(mk); got > 1 {
			t.Fatalf("%s became %d user turns — a concurrent frame was duplicated", mk, got)
		}
	}
	t.Logf("concurrent delivery: %d/%d became turns (no duplicates)", delivered(), n)
}

// A dropped connection must be recoverable in place — this is what a backend
// redeploy or a `cheese update` re-exec looks like from the session's side. The
// claude keeps running; only our end goes away and comes back.
func TestRendezvousReconnectsAndKeepsDelivering(t *testing.T) {
	f := startRendezvousClaude(t)
	const before = "RVRECON-001"
	if err := f.client.Reply(before + " 第一条：请运行那个工具。"); err != nil {
		t.Fatalf("first reply: %v", err)
	}
	waitFor(t, "the pre-drop prompt to become a user turn", 150*time.Second,
		func() bool { return f.userTurns(before) > 0 },
		func() { t.Logf("--- pane ---\n%s", f.pane()) })

	f.client.Close()
	if err := f.client.Reply("这条必须失败"); err == nil {
		t.Fatal("Reply on a closed client must fail rather than pretend")
	}

	// Redial the SAME socket with the SAME token — the token file is why an
	// adopted screen works, so a fresh connection must be accepted.
	c2, err := rendezvous.Dial(context.Background(), f.sock, f.token,
		rendezvous.Options{WaitForSocket: 30 * time.Second})
	if err != nil {
		t.Fatalf("redial after drop: %v", err)
	}
	defer c2.Close()

	const after = "RVRECON-002"
	if err := c2.Reply(after + " 第二条：请运行那个工具。"); err != nil {
		t.Fatalf("reply after redial: %v", err)
	}
	waitFor(t, "the post-redial prompt to become a user turn", 150*time.Second,
		func() bool { return f.userTurns(after) > 0 },
		func() { t.Logf("--- pane ---\n%s", f.pane()) })

	if got := f.userTurns(before); got != 1 {
		t.Fatalf("the pre-drop prompt became %d user turns, want 1", got)
	}
}

// The gate is load-bearing: without CLAUDE_BG_BACKEND=daemon there is no socket
// at all, and the connector must find that out as an error rather than assume a
// prompt was delivered. This is why the launcher refuses an old `claude`.
func TestRendezvousAbsentWithoutTheBackendSwitch(t *testing.T) {
	claudeBin := claudeBinary(t)
	work := realTempDir(t)
	home := realTempDir(t)
	configDir := filepath.Join(home, ".claude")
	if err := os.MkdirAll(configDir, 0o755); err != nil {
		t.Fatal(err)
	}
	gates := fmt.Sprintf(`{"hasCompletedOnboarding":true,"autoUpdates":false,`+
		`"bypassPermissionsModeAccepted":true,"projects":{%q:`+
		`{"hasTrustDialogAccepted":true,"hasCompletedProjectOnboarding":true}}}`, work)
	if err := os.WriteFile(filepath.Join(configDir, ".claude.json"), []byte(gates), 0o644); err != nil {
		t.Fatal(err)
	}
	sockDir, err := os.MkdirTemp("", "rv")
	if err != nil {
		t.Fatal(err)
	}
	defer os.RemoveAll(sockDir)
	sock := filepath.Join(sockDir, "s.sock")

	isolateTmux(t)
	m, err := terminal.NewManager()
	if err != nil {
		t.Fatalf("NewManager: %v", err)
	}
	defer m.KillServer()
	sess, err := m.Spawn("rv-nogate",
		[]string{"bash", "-lc", "cd " + work + " && exec " + claudeBin + " --dangerously-skip-permissions"},
		[]string{
			"HOME=" + home,
			"CLAUDE_CONFIG_DIR=" + configDir,
			"ANTHROPIC_AUTH_TOKEN=mock",
			"DISABLE_AUTOUPDATER=1",
			// Deliberately WITHOUT CLAUDE_BG_BACKEND.
			"CLAUDE_BG_RENDEZVOUS_SOCK=" + sock,
			"CLAUDE_BG_RV_AUTH=tok",
		}, paneCols, paneRows)
	if err != nil {
		t.Fatalf("Spawn: %v", err)
	}
	defer sess.Close()
	sess.OnChange(func() {}) // see the note in startRendezvousClaude

	_, err = rendezvous.Dial(context.Background(), sock, "tok",
		rendezvous.Options{WaitForSocket: 25 * time.Second})
	if err == nil {
		t.Fatal("a socket appeared without CLAUDE_BG_BACKEND=daemon — the gate moved")
	}
	if !strings.Contains(err.Error(), "did not appear") {
		t.Fatalf("expected a clear 'socket did not appear' error, got: %v", err)
	}
}

// Production shape: someone answers a decision request fifteen minutes later.
// The connection must still be there, and the session must still be beating —
// a socket that quietly went away between turns would be the new version of
// "the prompt vanished".
func TestRendezvousSurvivesAnIdleGap(t *testing.T) {
	if testing.Short() {
		t.Skip("idle-gap test takes ~90s")
	}
	f := startRendezvousClaude(t)

	const first = "RVIDLE-001"
	if err := f.client.Reply(first + " 第一条：请运行那个工具。"); err != nil {
		t.Fatalf("first reply: %v", err)
	}
	waitFor(t, "the first prompt to become a user turn", 120*time.Second,
		func() bool { return f.userTurns(first) > 0 },
		func() { t.Logf("--- pane ---\n%s", f.pane()) })

	// Three heartbeat intervals (30s each) with nothing sent.
	time.Sleep(95 * time.Second)

	if beats := f.frameCount("heartbeat"); beats < 2 {
		t.Fatalf("saw %d heartbeats across a 95s idle gap, want >= 2 — "+
			"liveness cannot be trusted without them", beats)
	}
	if !f.client.Alive() {
		t.Fatal("client reports dead after an ordinary idle gap")
	}

	const second = "RVIDLE-002"
	if err := f.client.Reply(second + " 第二条：请运行那个工具。"); err != nil {
		t.Fatalf("reply after idle gap: %v", err)
	}
	waitFor(t, "the post-gap prompt to become a user turn", 120*time.Second,
		func() bool { return f.userTurns(second) > 0 },
		func() { t.Logf("--- pane ---\n%s", f.pane()) })

	if got := f.userTurns(first); got != 1 {
		t.Fatalf("the first prompt became %d user turns after the gap, want 1", got)
	}
}
