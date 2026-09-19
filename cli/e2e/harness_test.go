//go:build claudee2e

// Shared fixtures for the delivery e2e suite: a scripted Anthropic API, a tmux
// server of the test's own, and a polling wait.
//
// The details here are hard-won (ported in spirit from micro-connector's
// testbed/e2e.sh): MockServer must be >= 7.5.0 (httpLlmResponse does not exist
// before it), the tool-call completion must be streamed (a non-streamed tool
// call is silently ignored, which looks exactly like nothing happening), the
// tool expectation matches on the tools list via JSON path (Claude Code also
// asks /v1/messages for a session title with no tools at all), and NO_PROXY
// must cover loopback (a developer machine's proxy would route the mock
// request away as ECONNRESET).
//
// The mock is what makes delivery PROVABLE rather than plausible: it answers a
// delivered prompt with a scripted Bash tool call, and the file that tool
// writes is the evidence. A prompt that reaches the session but is never
// enqueued leaves a pane that looks perfect and a file that never grows.
package e2e

import (
	"bytes"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"strings"
	"testing"
	"time"
)

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

// The image the mock API runs from. CI pulls it ahead of the suite (through the
// registry mirror, with retries) and tags it under this name, so the run here
// never reaches Docker Hub itself.
const mockImage = "mockserver/mockserver:mockserver-7.5.0"

// startMockAPI runs MockServer in docker on a random loopback port and scripts
// the model: any /v1/messages that offers a Bash tool gets a streamed tool
// call that writes markFile; everything else (the title request) ends the turn.
func startMockAPI(t *testing.T, markFile, mark string) string {
	t.Helper()
	name := fmt.Sprintf("cheese-e2e-mock-%d", time.Now().UnixNano())
	out, err := exec.Command("docker", "run", "-d", "--name", name,
		"-p", "127.0.0.1::1080", mockImage).CombinedOutput()
	if err != nil {
		// On a laptop without docker the suite steps aside. In CI it must not:
		// the canary ran two nights (2026-09-17, -18) with seven of eight tests
		// skipped because the image pull failed, and reported green — a
		// canary that skips is the silent one claude-canary.yml warns about.
		if os.Getenv("CHEESE_E2E_REQUIRE_DOCKER") != "" {
			t.Fatalf("docker unavailable for the mock API: %v: %s", err, out)
		}
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

// isolateTmux points terminal.NewManager at a runtime dir of this test's own.
// NewManager derives its socket from $TMPDIR, and on a machine that hosts agents
// the default one is the LIVE connector's server — which holds every running
// `claude` on the box, and which these tests kill on the way out. A short path
// on purpose: a unix socket path is capped near 104 bytes.
func isolateTmux(t *testing.T) {
	t.Helper()
	dir, err := os.MkdirTemp("/tmp", "cheesee2e")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(dir) })
	t.Setenv("TMPDIR", dir)
}
