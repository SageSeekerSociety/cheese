//go:build claudee2e && linux

package e2e

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/creack/pty"

	"github.com/SageSeekerSociety/cheese/cli/internal/config"
	"github.com/SageSeekerSociety/cheese/cli/internal/place"
)

type anthropicStub struct {
	address string
	ca      string
}

func startAnthropicStub(t *testing.T, meter, python, toolCommand string) anthropicStub {
	t.Helper()
	tool, err := json.Marshal(map[string]any{
		"name": "Bash",
		"input": map[string]string{
			"command": toolCommand, "description": "Check sandbox networking and write test marker",
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
	cmd := exec.CommandContext(ctx, "node", "../../e2e/stub-anthropic.mjs")
	cmd.Cancel = func() error { return cmd.Process.Signal(syscall.SIGTERM) }
	cmd.WaitDelay = 2 * time.Second
	cmd.Env = append(os.Environ(),
		"TMPDIR="+t.TempDir(),
		"STUB_ANTHROPIC_PORT=0",
		"STUB_ANTHROPIC_PLAN=max",
		"STUB_ANTHROPIC_METER="+meter,
		"STUB_ANTHROPIC_RATE_LIMIT=allowed",
		"STUB_ANTHROPIC_RATE_LIMIT_AFTER_REFRESH=rejected",
		"STUB_ANTHROPIC_TOOL="+string(tool),
	)
	cmd.Stderr = os.Stderr
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		cancel()
		t.Fatal(err)
	}
	if err := cmd.Start(); err != nil {
		cancel()
		t.Fatal(err)
	}
	lines := bufio.NewScanner(stdout)
	var stub anthropicStub
	if lines.Scan() {
		stub.address, _ = strings.CutPrefix(lines.Text(), "HTTPS_PROXY=")
	}
	if lines.Scan() {
		stub.ca, _ = strings.CutPrefix(lines.Text(), "NODE_EXTRA_CA_CERTS=")
	}
	drained := make(chan struct{})
	var meteredRequests []string
	go func() {
		defer close(drained)
		for lines.Scan() {
			line := lines.Text()
			t.Log(line)
			if strings.HasSuffix(line, ", placeholder)") || strings.HasSuffix(line, ", other)") {
				meteredRequests = append(meteredRequests, line)
			}
		}
	}()
	t.Cleanup(func() {
		cancel()
		<-drained
		_ = cmd.Wait()
		if meter != "null" {
			if len(meteredRequests) == 0 {
				t.Error("meter did not observe any HTTP requests")
				return
			}
			payload, err := json.Marshal(meteredRequests)
			if err != nil {
				t.Error(err)
				return
			}
			audit := exec.Command(python, "subscription_sandbox.py", "audit-meter", meter)
			audit.Stdin = bytes.NewReader(payload)
			if out, err := audit.CombinedOutput(); err != nil {
				t.Errorf("%s%v", out, err)
			}
		}
	})
	if stub.address == "" || stub.ca == "" {
		t.Fatal("Anthropic stub did not announce its listener and CA")
	}
	return stub
}

func backendPython(t *testing.T) string {
	t.Helper()
	p := os.Getenv("CHEESE_E2E_PYTHON")
	if p == "" {
		p = "../../backend/.venv/bin/python"
	}
	path, err := filepath.Abs(p)
	if err != nil {
		t.Fatal(err)
	}
	return path
}

func stageProduction(t *testing.T, python string, args ...string) string {
	t.Helper()
	cmd := exec.Command(python, append([]string{"subscription_sandbox.py"}, args...)...)
	cmd.Env = []string{"PATH=" + os.Getenv("PATH"), "PYTHONDONTWRITEBYTECODE=1"}
	out, err := cmd.Output()
	if err != nil {
		if failure, ok := err.(*exec.ExitError); ok {
			t.Logf("%s", failure.Stderr)
		}
		t.Fatalf("prepare production launch: %v\n%s", err, out)
	}
	return strings.TrimSpace(string(out))
}

func cheesehostBinary(t *testing.T) string {
	t.Helper()
	if p := os.Getenv("CHEESE_E2E_CHEESEHOST"); p != "" {
		return p
	}
	binary := filepath.Join(t.TempDir(), "cheesehost")
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
	defer cancel()
	cmd := exec.CommandContext(ctx, "go", "build", "-o", binary, ".")
	cmd.Dir = ".."
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("build cheesehost: %v\n%s", err, out)
	}
	return binary
}

func loginOnHost(t *testing.T, binary, cheesehost, owner string, stub anthropicStub) {
	t.Helper()
	pin := filepath.Join(owner, place.Root, "claude", "versions", pinnedVersion)
	if err := os.MkdirAll(filepath.Dir(pin), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(binary, pin); err != nil {
		t.Fatal(err)
	}

	connector := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/connector/claude/pin" {
			http.NotFound(w, r)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]string{"version": pinnedVersion})
	}))
	t.Cleanup(connector.Close)
	cfg := filepath.Join(owner, "cheese.json")
	if err := config.Save(cfg, &config.Config{Base: connector.URL}); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 25*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, cheesehost, "claude", "login", "--login", place.FirstClaudeLogin, "--config", cfg)
	cmd.Cancel = func() error { return syscall.Kill(-cmd.Process.Pid, syscall.SIGTERM) }
	cmd.Dir = owner
	cmd.Env = []string{
		"PATH=" + os.Getenv("PATH"), "HOME=" + owner,
		"BROWSER=/usr/bin/false", "HTTPS_PROXY=" + stub.address,
		"NODE_EXTRA_CA_CERTS=" + stub.ca,
	}
	terminal, err := pty.Start(cmd)
	if err != nil {
		t.Fatal(err)
	}
	defer terminal.Close()
	waited := make(chan error, 1)
	go func() { waited <- cmd.Wait(); close(waited) }()
	defer func() {
		cancel()
		<-waited
	}()

	var transcript bytes.Buffer
	defer func() { t.Logf("cheesehost claude login --login %s\n%s", place.FirstClaudeLogin, transcript.String()) }()
	reader := bufio.NewReader(io.TeeReader(terminal, &transcript))
	const prefix = "If the browser didn't open, visit: "
	var authorizeURL string
	for authorizeURL == "" {
		line, err := reader.ReadString('\n')
		if err != nil {
			t.Fatalf("read login authorize URL: %v", err)
		}
		_, link, found := strings.Cut(line, prefix)
		if found {
			if target, hyperlink := strings.CutPrefix(link, "\x1b]8;;"); hyperlink {
				link, _, _ = strings.Cut(target, "\a")
			}
			authorizeURL = strings.TrimSpace(link)
		}
	}

	body, err := exec.CommandContext(ctx, "/usr/bin/curl", "-fsSL", "--max-time", "8",
		"--proxy", stub.address, "--noproxy", "", "--cacert", stub.ca, authorizeURL).CombinedOutput()
	if err != nil {
		t.Fatalf("fetch authorize URL through proxy: %v\n%s", err, body)
	}
	t.Logf("authorize callback\n%s", body)
	_, after, hasStart := strings.Cut(string(body), "<code>")
	code, _, hasEnd := strings.Cut(after, "</code>")
	if !hasStart || !hasEnd || code == "" {
		t.Fatal("authorize page did not return a code")
	}
	if _, err := io.WriteString(terminal, code+"\n"); err != nil {
		t.Fatalf("paste authorization code: %v", err)
	}
	// A Unix PTY closes with EIO after its child exits.
	_, _ = io.Copy(io.Discard, reader)
	if err := <-waited; err != nil {
		t.Fatalf("cheesehost login failed: %v", err)
	}
	if !strings.Contains(transcript.String(), "Login successful.") {
		t.Fatal("cheesehost login did not complete OAuth")
	}
}

func runSubscription(t *testing.T, python, caseName, binary, home, action, prompt string, proxySettings ...string) (string, error) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 25*time.Second)
	defer cancel()
	args := append([]string{"subscription_sandbox.py", "run", home, caseName, binary, action}, proxySettings...)
	cmd := exec.CommandContext(ctx, python, args...)
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	cmd.Cancel = func() error { return syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL) }
	if prompt != "" {
		payload, err := json.Marshal(map[string]any{
			"type": "user", "message": map[string]string{"role": "user", "content": prompt},
		})
		if err != nil {
			t.Fatal(err)
		}
		cmd.Stdin = strings.NewReader(string(payload) + "\n")
	}
	out, err := cmd.CombinedOutput()
	t.Logf("%s\n%s", action, out)
	return string(out), err
}

func sessionMountNamespace(t *testing.T, output string) string {
	t.Helper()
	for _, line := range strings.Split(output, "\n") {
		if audit, ok := strings.CutPrefix(line, "SESSION_ENV "); ok {
			var session struct {
				MountNamespace string `json:"mount_namespace"`
			}
			if err := json.Unmarshal([]byte(audit), &session); err != nil || session.MountNamespace == "" {
				t.Fatalf("invalid Claude process namespace audit: %v", err)
			}
			return session.MountNamespace
		}
	}
	t.Fatal("missing Claude process namespace audit")
	return ""
}

// ownerHome is the home of the machine's owner, where the owner's Claude login
// lives. It is outside /tmp, which the sandbox replaces with the room's own,
// so that only hiding the owner's home keeps the login out of the sandbox.
func ownerHome(t *testing.T) string {
	t.Helper()
	parent, err := filepath.Abs(filepath.Join("..", "..", "tmp"))
	if err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(parent, 0o755); err != nil {
		t.Fatal(err)
	}
	dir, err := os.MkdirTemp(parent, "subscription-owner-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(dir) })
	resolved, err := filepath.EvalSymlinks(dir)
	if err != nil {
		t.Fatal(err)
	}
	return resolved
}

// stagedCredentials is the owner's login the staged launch signs in with.
func stagedCredentials(t *testing.T, home string) string {
	t.Helper()
	data, err := os.ReadFile(filepath.Join(home, "production.json"))
	if err != nil {
		t.Fatal(err)
	}
	var staged struct {
		Env map[string]string `json:"env"`
	}
	if err := json.Unmarshal(data, &staged); err != nil {
		t.Fatal(err)
	}
	dir := staged.Env["CLAUDE_SECURESTORAGE_CONFIG_DIR"]
	if dir == "" {
		t.Fatal("own-login launch names no credential store")
	}
	return filepath.Join(dir, ".credentials.json")
}

// expireCredential sets the stored access token's expiry to now, so the
// session's next request refreshes it first.
func expireCredential(t *testing.T, path string) {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var stored map[string]json.RawMessage
	var oauth map[string]any
	if err := json.Unmarshal(data, &stored); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(stored["claudeAiOauth"], &oauth); err != nil || oauth["expiresAt"] == nil {
		t.Fatalf("stored login has no OAuth expiry: %v\n%s", err, data)
	}
	oauth["expiresAt"] = time.Now().UnixMilli()
	if stored["claudeAiOauth"], err = json.Marshal(oauth); err != nil {
		t.Fatal(err)
	}
	if data, err = json.Marshal(stored); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, data, 0o600); err != nil {
		t.Fatal(err)
	}
}

func TestClaudeSubscriptionStub(t *testing.T) {
	binary, err := filepath.Abs(claudeBinary(t))
	if err != nil {
		t.Fatal(err)
	}
	binary, err = filepath.EvalSymlinks(binary)
	if err != nil {
		t.Fatal(err)
	}
	version := exec.Command(binary, "--version")
	version.Env = []string{"PATH=" + os.Getenv("PATH")}
	if out, err := version.Output(); err != nil || !strings.HasPrefix(string(out), pinnedVersion+" ") {
		t.Fatalf("expected Claude Code %s, got %q (%v)", pinnedVersion, out, err)
	}

	python := backendPython(t)
	hostMount, err := os.Readlink("/proc/self/ns/mnt")
	if err != nil {
		t.Fatal(err)
	}
	for _, caseName := range []string{"metered", "metered-no-credential", "owned"} {
		t.Run(caseName, func(t *testing.T) {
			home := realTempDir(t)
			owner := ownerHome(t)
			prepared, err := exec.Command(python, "subscription_sandbox.py", "prepare", home).CombinedOutput()
			if err != nil {
				t.Fatalf("prepare sandbox: %v\n%s", err, prepared)
			}
			var paths struct{ Platform, Sockets string }
			if err := json.Unmarshal(prepared, &paths); err != nil {
				t.Fatalf("read sandbox paths: %v\n%s", err, prepared)
			}
			t.Cleanup(func() {
				_ = os.RemoveAll(paths.Platform)
				_ = os.RemoveAll(paths.Sockets)
				_ = os.Remove(paths.Sockets + ".sock")
			})

			// The host's listener separates the platform network from the enrolled device network.
			outside, err := net.Listen("tcp4", "127.0.0.2:0")
			if err != nil {
				t.Fatal(err)
			}
			hostService := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				w.WriteHeader(http.StatusNoContent)
			})}
			go func() { _ = hostService.Serve(outside) }()
			t.Cleanup(func() { _ = hostService.Close() })
			hostClient := &http.Client{Timeout: 2 * time.Second}
			response, err := hostClient.Get("http://" + outside.Addr().String())
			if err != nil {
				t.Fatalf("probe host listener: %v", err)
			}
			response.Body.Close()
			if response.StatusCode != http.StatusNoContent {
				t.Fatalf("probe host listener: %s", response.Status)
			}
			owned := caseName == "owned"
			curlExit := 7
			if owned {
				curlExit = 0
			}
			tool := fmt.Sprintf(`/usr/bin/readlink /proc/self/ns/mnt > tool-mount.txt
/usr/bin/curl --noproxy '*' --max-time 2 -fsS http://%s > direct-result.txt 2>&1
status=$?
printf 'curl exit %%s\n' "$status" >> direct-result.txt
test "$status" -eq %d && printf stub-tool-ran > tool-result.txt`, outside.Addr(), curlExit)
			if owned {
				tool = fmt.Sprintf(`test -n "$CLAUDE_SECURESTORAGE_CONFIG_DIR" || exit 1
test -e "$CLAUDE_SECURESTORAGE_CONFIG_DIR/.credentials.json"
printf 'login visible exit %%s\n' "$?" > tool-login.txt
test -d '%s'
printf 'owner parent visible exit %%s\n' "$?" >> tool-login.txt
`, filepath.Dir(owner)) + tool
			}
			meter := "null"
			var cheesehost string
			if owned {
				cheesehost = cheesehostBinary(t)
			} else {
				meter = stageProduction(t, python, "meter", caseName, tableProject, tableTopic)
			}
			stub := startAnthropicStub(t, meter, python, tool)
			stageProduction(t, python, "stage", home, caseName, stub.ca, tableProject, tableTopic, owner)
			// The stub stands in for the metering proxy's tunnel on a metered
			// session, and for Anthropic itself on an owned one.
			proxySettings := []string{stub.address}
			var credentials string
			if owned {
				proxySettings = append(proxySettings, stub.ca)
				loginOnHost(t, binary, cheesehost, owner, stub)
				credentials = stagedCredentials(t, home)
				if !strings.HasPrefix(credentials, owner+string(filepath.Separator)) {
					t.Fatalf("login %s is not in the owner's home %s", credentials, owner)
				}
				if _, err := os.Stat(credentials); err != nil {
					t.Fatalf("expected the login on the host: %v", err)
				}
				status, err := runSubscription(t, python, caseName, binary, home, "status", "", proxySettings...)
				var account struct {
					LoggedIn         bool   `json:"loggedIn"`
					AuthMethod       string `json:"authMethod"`
					SubscriptionType string `json:"subscriptionType"`
				}
				if err != nil || json.Unmarshal([]byte(status), &account) != nil ||
					!account.LoggedIn || account.AuthMethod != "claude.ai" || account.SubscriptionType != "max" {
					t.Fatalf("owned session did not pick up the host's subscription: %v\n%s", err, status)
				}
			}
			out, err := runSubscription(t, python, caseName, binary, home, "turn", "Write the marker", proxySettings...)
			modelMount := sessionMountNamespace(t, out)
			if (modelMount == hostMount) != owned {
				t.Fatalf("Claude mount namespace %s has the wrong relationship to host %s for %s", modelMount, hostMount, caseName)
			}
			if caseName == "metered-no-credential" {
				if !strings.Contains(out, "the platform has no Claude login") {
					t.Fatalf("expected the meter's missing-credential refusal: %v\n%s", err, out)
				}
				return
			}
			if err != nil || !strings.Contains(out, `"result":"Hi there!"`) {
				t.Fatalf("expected completed tool turn: %v\n%s", err, out)
			}
			workspace := filepath.Join(home, "workspace")
			marker, err := os.ReadFile(filepath.Join(workspace, "tool-result.txt"))
			if err != nil || string(marker) != "stub-tool-ran" {
				t.Fatalf("expected tool to write its marker, got %q (%v)", marker, err)
			}
			direct, err := os.ReadFile(filepath.Join(workspace, "direct-result.txt"))
			if err != nil {
				t.Fatal(err)
			}
			toolMount, err := os.ReadFile(filepath.Join(workspace, "tool-mount.txt"))
			toolNamespace := strings.TrimSpace(string(toolMount))
			if err != nil || !strings.HasPrefix(toolNamespace, "mnt:[") || toolNamespace == hostMount {
				t.Fatalf("expected the Bash tool to run in a separate mount namespace, got %q (%v)", toolMount, err)
			}
			if !owned && modelMount == toolNamespace {
				t.Fatalf("expected the metered model outside the room's sandbox, both in %s", modelMount)
			}
			t.Logf("Mount namespaces host=%s model=%s tool=%s", hostMount, modelMount, toolNamespace)
			t.Logf("Host listener %s -> %s; tool inside sandbox:\n%s", outside.Addr(), response.Status, direct)
			if owned {
				storage, err := os.ReadFile(filepath.Join(workspace, "tool-login.txt"))
				if err != nil || string(storage) != "login visible exit 1\nowner parent visible exit 0\n" {
					t.Fatalf("expected the owner's home hidden from the tool and its parent shown, got %q (%v)", storage, err)
				}
				t.Logf("Bash login visibility probe (login %s):\n%s", credentials, storage)
				expireCredential(t, credentials)
				out, _ := runSubscription(t, python, caseName, binary, home, "turn", "say hi", proxySettings...)
				if !strings.Contains(out, "You've hit your 5-hour limit") {
					t.Fatalf("expected subscription limit after refresh\n%s", out)
				}
			}
		})
	}
}
