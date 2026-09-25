//go:build claudee2e

// A REAL Claude Code, started the way a room's session is started — headless,
// stream-json on both pipes — against the scripted Anthropic API in
// harness_test.go. The mock records every request the boot makes, which is
// what control_table_e2e_test.go holds the metering proxy's answer table to.
//
// What is proven, not assumed: the file the scripted tool writes. A session that
// took the message but never ran the turn leaves a file that never grows.
package e2e

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// realTempDir is t.TempDir() with symlinks resolved: on macOS the temp root is
// /var/..., a symlink to /private/var/..., and Claude Code keys its project
// directory on the RESOLVED cwd.
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
const pinnedVersion = "2.1.282"

type bootFixture struct {
	markFile string
	mark     string
	apiBase  string
	stderr   *bytes.Buffer
}

// startHeadlessClaude boots a real Claude Code in its own HOME and config dir
// and writes one user message to its stdin.
func startHeadlessClaude(t *testing.T, message string) *bootFixture {
	t.Helper()
	claudeBin := claudeBinary(t)

	work := realTempDir(t)
	markFile := filepath.Join(work, "heard.txt")
	mark := fmt.Sprintf("claude-acted-%d", time.Now().UnixNano())
	apiBase := startMockAPI(t, markFile, mark)

	home := realTempDir(t)
	configDir := filepath.Join(home, ".claude")
	if err := os.MkdirAll(configDir, 0o755); err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command(claudeBin,
		"-p", "--input-format", "stream-json", "--output-format", "stream-json",
		"--verbose", "--permission-mode", "bypassPermissions",
		"--setting-sources", "user")
	cmd.Dir = work
	cmd.Env = []string{
		"PATH=" + os.Getenv("PATH"),
		"HOME=" + home,
		"CLAUDE_CONFIG_DIR=" + configDir,
		"ANTHROPIC_BASE_URL=" + apiBase,
		"ANTHROPIC_AUTH_TOKEN=mock",
		"ANTHROPIC_MODEL=claude-sonnet-4-5",
		"DISABLE_AUTOUPDATER=1",
		"NO_PROXY=127.0.0.1,localhost",
		"no_proxy=127.0.0.1,localhost",
	}
	stdin, err := cmd.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	cmd.Stdout = io.Discard
	stderr := &bytes.Buffer{}
	cmd.Stderr = stderr
	if err := cmd.Start(); err != nil {
		t.Fatalf("start claude: %v", err)
	}
	t.Cleanup(func() {
		_ = stdin.Close()
		done := make(chan struct{})
		go func() { _ = cmd.Wait(); close(done) }()
		select {
		case <-done:
		case <-time.After(10 * time.Second):
			_ = cmd.Process.Kill()
			<-done
		}
	})
	line, err := json.Marshal(map[string]any{
		"type":               "user",
		"message":            map[string]any{"role": "user", "content": message},
		"parent_tool_use_id": nil,
		"session_id":         "",
	})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := stdin.Write(append(line, '\n')); err != nil {
		t.Fatalf("write the message: %v", err)
	}
	return &bootFixture{markFile: markFile, mark: mark, apiBase: apiBase, stderr: stderr}
}

func (f *bootFixture) marks() int {
	b, err := os.ReadFile(f.markFile)
	if err != nil {
		return 0
	}
	return strings.Count(string(b), f.mark)
}

// lastConversationBody is the most recent CONVERSATION request's body as JSON of
// its own — the object Claude Code posted, member order preserved, rather than
// MockServer's envelope around it. A conversation request is the one that
// offers the model its tools; a session-title side request offers none.
func (f *bootFixture) lastConversationBody() ([]byte, error) {
	recorded, err := f.retrieve("type=requests")
	if err != nil {
		return nil, err
	}
	var last []byte
	for _, r := range recorded {
		if !strings.Contains(r.Path, "/v1/messages") {
			continue
		}
		if body := r.conversationBody(); len(body) > 0 {
			last = body
		}
	}
	if last == nil {
		return nil, fmt.Errorf("the mock recorded no conversation request")
	}
	return last, nil
}

// recordedExchanges pairs every request the mock saw with the status it
// answered — including the ones no expectation matched, which are exactly the
// paths the proxy's answer table would have to grow a row for.
func (f *bootFixture) recordedExchanges() ([]recordedExchange, error) {
	raw, err := f.put("/mockserver/retrieve?type=request_responses&format=json")
	if err != nil {
		return nil, err
	}
	var pairs []struct {
		Request struct {
			Method string `json:"method"`
			Path   string `json:"path"`
		} `json:"httpRequest"`
		Response struct {
			StatusCode int `json:"statusCode"`
		} `json:"httpResponse"`
	}
	if err := json.Unmarshal(raw, &pairs); err != nil {
		return nil, fmt.Errorf("decode recorded request/response pairs: %w", err)
	}
	out := make([]recordedExchange, 0, len(pairs))
	for _, p := range pairs {
		out = append(out, recordedExchange{
			method: p.Request.Method,
			path:   p.Request.Path,
			status: p.Response.StatusCode,
		})
	}
	return out, nil
}

type recordedExchange struct {
	method string
	path   string
	status int
}

type recordedRequest struct {
	Path string          `json:"path"`
	Body json.RawMessage `json:"body"`
}

// conversationBody returns the posted object when this request was a
// CONVERSATION request, and nothing when it was one of Claude Code's own side
// requests (a session title, which offers the model no tools at all).
func (r recordedRequest) conversationBody() []byte {
	var body struct {
		JSON json.RawMessage `json:"json"`
	}
	if json.Unmarshal(r.Body, &body) != nil || len(body.JSON) == 0 {
		return nil
	}
	var offered struct {
		Tools []json.RawMessage `json:"tools"`
	}
	if json.Unmarshal(body.JSON, &offered) != nil || len(offered.Tools) == 0 {
		return nil
	}
	return body.JSON
}

func (f *bootFixture) retrieve(query string) ([]recordedRequest, error) {
	raw, err := f.put("/mockserver/retrieve?" + query + "&format=json")
	if err != nil {
		return nil, err
	}
	var recorded []recordedRequest
	if err := json.Unmarshal(raw, &recorded); err != nil {
		return nil, fmt.Errorf("decode recorded requests: %w", err)
	}
	return recorded, nil
}

func (f *bootFixture) put(path string) ([]byte, error) {
	req, err := http.NewRequest(http.MethodPut, f.apiBase+path, nil)
	if err != nil {
		return nil, err
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	return io.ReadAll(resp.Body)
}
