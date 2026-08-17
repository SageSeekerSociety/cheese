//go:build claudee2e

// Package e2e drives a REAL Claude Code through the exact stack a device screen
// uses — the goja runtime, the shipped claude_min.js cheeselet, and the tmux
// terminal layer with its chunked writes — against a mock Anthropic API.
//
// Ported in spirit from micro-teams/micro-connector's testbed/e2e.sh, which is
// where every hard-won detail below comes from: MockServer must be >= 7.5.0
// (httpLlmResponse does not exist before it), the tool-call completion must be
// streamed (a non-streamed tool call is silently ignored, which looks exactly
// like nothing happening), the tool expectation matches on the tools list via
// JSON path (Claude Code also asks /v1/messages for a session title with no
// tools at all), and NO_PROXY must cover loopback (a developer machine's proxy
// would route the mock request away as ECONNRESET).
//
// What only this test can assert: that a prompt handed to the cheeselet is
// pasted, visibly lands, is submitted, and Claude Code ACTS on it — the mock
// answers with a scripted Bash tool call and the file that tool writes is the
// proof. An Enter swallowed into the paste leaves a pane that looks perfect
// and does nothing; a screenshot cannot tell the difference, the file can.
//
// Gated behind the claudee2e build tag: it needs a real `claude` on PATH and a
// running docker daemon, and skips itself when either is missing.
package e2e

import (
	"bytes"
	"context"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/SageSeekerSociety/cheese/cli/internal/runtime"
	"github.com/SageSeekerSociety/cheese/cli/internal/terminal"
)

// stubBus satisfies runtime.Bus; the control plane is not under test here.
type stubBus struct{ inbound runtime.Inbound }

func (b *stubBus) PushVar(string, any)              {}
func (b *stubBus) CallServer(string, string, []any) {}
func (b *stubBus) ReplyServer(string, any, string)  {}
func (b *stubBus) SetInbound(i runtime.Inbound)     { b.inbound = i }

func mockserverPut(t *testing.T, base, path, body string) {
	t.Helper()
	req, err := http.NewRequest(http.MethodPut, base+path, strings.NewReader(body))
	if err != nil {
		t.Fatalf("build %s: %v", path, err)
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("PUT %s: %v", path, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 300 {
		var buf bytes.Buffer
		_, _ = buf.ReadFrom(resp.Body)
		t.Fatalf("PUT %s: %d: %s", path, resp.StatusCode, buf.String())
	}
}

// startMockAPI runs MockServer in docker on a random loopback port and scripts
// the model: any /v1/messages that offers a Bash tool gets a streamed tool
// call that writes markFile; everything else (the title request) ends the turn.
func startMockAPI(t *testing.T, markFile, mark string) string {
	t.Helper()
	name := fmt.Sprintf("cheese-e2e-mock-%d", time.Now().UnixNano())
	out, err := exec.Command("docker", "run", "-d", "--name", name,
		"-p", "127.0.0.1::1080", "mockserver/mockserver:mockserver-7.5.0").CombinedOutput()
	if err != nil {
		t.Skipf("docker unavailable for the mock API: %v: %s", err, out)
	}
	t.Cleanup(func() { _ = exec.Command("docker", "rm", "-f", name).Run() })

	port, err := exec.Command("docker", "port", name, "1080/tcp").Output()
	if err != nil {
		t.Fatalf("docker port: %v", err)
	}
	hostPort := strings.TrimSpace(string(port))
	hostPort = hostPort[strings.LastIndex(hostPort, ":")+1:]
	base := "http://127.0.0.1:" + hostPort

	deadline := time.Now().Add(90 * time.Second)
	for {
		req, _ := http.NewRequest(http.MethodPut, base+"/mockserver/status", nil)
		if resp, err := http.DefaultClient.Do(req); err == nil {
			resp.Body.Close()
			if resp.StatusCode < 300 {
				break
			}
		}
		if time.Now().After(deadline) {
			t.Fatalf("mockserver never came up on %s", base)
		}
		time.Sleep(time.Second)
	}

	// remainingTimes 2: the short prompt and the 30KB prompt each spend one.
	mockserverPut(t, base, "/mockserver/expectation", fmt.Sprintf(`
{ "httpRequest": { "method": "POST", "path": "/v1/messages",
                   "body": { "type": "JSON_PATH", "jsonPath": "$.tools[?(@.name=='Bash')]" } },
  "times": { "remainingTimes": 2, "unlimited": false },
  "priority": 10,
  "httpLlmResponse": { "provider": "ANTHROPIC", "model": "claude-sonnet-4-5",
    "completion": { "text": "Doing it.", "streaming": true, "stopReason": "tool_use",
      "toolCalls": [ { "id": "toolu_e2e_1", "name": "Bash",
        "arguments": "{\"command\":\"echo %s >> %s\",\"description\":\"reply\"}" } ],
      "usage": { "inputTokens": 100, "outputTokens": 20 } } } }`, mark, markFile))
	mockserverPut(t, base, "/mockserver/expectation", `
{ "httpRequest": { "method": "POST", "path": "/v1/messages" },
  "priority": 1,
  "httpLlmResponse": { "provider": "ANTHROPIC", "model": "claude-sonnet-4-5",
    "completion": { "text": "done", "streaming": true, "stopReason": "end_turn",
                    "usage": { "inputTokens": 30, "outputTokens": 3 } } } }`)
	return base
}

func waitFor(t *testing.T, what string, timeout time.Duration, cond func() bool, onFail func()) {
	t.Helper()
	deadline := time.Now().Add(timeout)
	for !cond() {
		if time.Now().After(deadline) {
			if onFail != nil {
				onFail()
			}
			t.Fatalf("timed out waiting for %s", what)
		}
		time.Sleep(500 * time.Millisecond)
	}
}

func TestRealClaudeActsOnDeliveredPrompts(t *testing.T) {
	claudeBin, err := exec.LookPath("claude")
	if err != nil {
		t.Skip("no `claude` on PATH")
	}

	work := realTempDir(t)
	markFile := filepath.Join(work, "heard.txt")
	mark := fmt.Sprintf("claude-acted-%d", time.Now().UnixNano())
	apiBase := startMockAPI(t, markFile, mark)

	// The gates, exactly as the production launcher pre-accepts them
	// (device_launch.py) — there is no gate-answering driver here, so a gate
	// that these no longer satisfy hangs the readiness wait below. That hang
	// IS the intelligence this test exists to produce when Anthropic changes
	// the first-run flow.
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

	m, err := terminal.NewManager()
	if err != nil {
		t.Fatalf("NewManager: %v", err)
	}
	defer m.KillServer()

	// Absolute claude path: the program starts through a login shell, which
	// rebuilds PATH from the profile — a PATH handed as screen env does not
	// survive it (upstream: the symptom is "Pane is dead (status 127)").
	sess, err := m.Spawn("e2e",
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
		}, 120, 32)
	if err != nil {
		t.Fatalf("Spawn: %v", err)
	}
	defer sess.Close()

	pane := func() string { return sess.Snapshot() }
	dumpPane := func() { t.Logf("--- pane ---\n%s", pane()) }

	// The runtime hosting the SHIPPED cheeselet — the same file the backend
	// hands every device screen. CHEESELET_SRC overrides the monorepo-relative
	// default so the compiled test can run on a machine that has no checkout.
	cheeseletPath := os.Getenv("CHEESELET_SRC")
	if cheeseletPath == "" {
		cheeseletPath = filepath.Join("..", "..",
			"backend", "app", "domain", "agent", "cheeselets", "claude_min.js")
	}
	src, err := os.ReadFile(cheeseletPath)
	if err != nil {
		t.Fatalf("read cheeselet: %v", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	rt := runtime.New(sess, &stubBus{})
	go func() { _ = rt.Run(ctx) }()
	rt.LoadScript(string(src))

	waitFor(t, "Claude Code to reach a working prompt", 120*time.Second, func() bool {
		s := pane()
		return strings.Contains(s, "❯") &&
			!strings.Contains(s, "trust this folder") &&
			!strings.Contains(s, "bypass permissions?")
	}, dumpPane)

	countMarks := func() int {
		b, err := os.ReadFile(markFile)
		if err != nil {
			return 0
		}
		return strings.Count(string(b), mark)
	}

	// Leg 1: a short prompt must be pasted, land, be submitted, and be ACTED on.
	rt.Invoke("call-1", "prompt", []any{"run the tool"})
	waitFor(t, "the first prompt to be acted on (tool ran)", 120*time.Second,
		func() bool { return countMarks() >= 1 }, dumpPane)

	// Leg 2: a message far past tmux's single-command limit. Before the
	// chunked-write fix (upstream T-058) this died with "command too long" in
	// a log nobody reads and claude never heard anything at all; the closed-
	// loop cheeselet plus chunking must land and submit it just the same.
	long := "run the tool. " + strings.Repeat("这是一条用来撑过 tmux 单条命令上限的长消息。", 900)
	rt.Invoke("call-2", "prompt", []any{long})
	waitFor(t, "the 30KB prompt to be acted on (tool ran again)", 180*time.Second,
		func() bool { return countMarks() >= 2 }, dumpPane)
}
