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
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/creack/pty"
)

func startAnthropicStub(t *testing.T, platform, plan, limit string, expiresIn int, toolCommand string) {
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
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	cmd := exec.CommandContext(ctx, "node", "../../e2e/stub-anthropic.mjs")
	cmd.Cancel = func() error { return cmd.Process.Signal(syscall.SIGTERM) }
	cmd.WaitDelay = 2 * time.Second
	socket := filepath.Join(platform, "forge.sock")
	cmd.Env = append(os.Environ(),
		"TMPDIR="+t.TempDir(),
		"STUB_ANTHROPIC_SOCKET="+socket,
		"STUB_ANTHROPIC_PLAN="+plan,
		"STUB_ANTHROPIC_RATE_LIMIT="+limit,
		"STUB_ANTHROPIC_RATE_LIMIT_AFTER_REFRESH=rejected",
		"STUB_ANTHROPIC_EXPIRES_IN="+strconv.Itoa(expiresIn),
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
	ready := lines.Scan() && lines.Text() == "STUB_ANTHROPIC_SOCKET="+socket
	var ca string
	if lines.Scan() {
		ca, _ = strings.CutPrefix(lines.Text(), "NODE_EXTRA_CA_CERTS=")
	}
	drained := make(chan struct{})
	go func() {
		defer close(drained)
		for lines.Scan() {
			t.Log(lines.Text())
		}
	}()
	t.Cleanup(func() {
		cancel()
		<-drained
		_ = cmd.Wait()
	})
	if !ready || ca == "" {
		t.Fatal("Anthropic stub did not announce its socket and CA")
	}
	t.Logf("STUB_ANTHROPIC_SOCKET=%s", socket)
	pem, err := os.ReadFile(ca)
	if err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"ca.pem", "ca-bundle.pem"} {
		if err := os.WriteFile(filepath.Join(platform, name), pem, 0o644); err != nil {
			t.Fatal(err)
		}
	}
}

type subscriptionSandbox struct {
	binary string
	home   string
}

func (sandbox subscriptionSandbox) command(ctx context.Context, args ...string) *exec.Cmd {
	base := []string{"subscription_sandbox.py", "run", sandbox.home, sandbox.binary}
	return exec.CommandContext(ctx, "python3", append(base, args...)...)
}

func (sandbox subscriptionSandbox) login(t *testing.T) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 25*time.Second)
	defer cancel()
	cmd := sandbox.command(ctx, sandbox.binary, "auth", "login", "--claudeai")
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
	defer func() { t.Logf("claude auth login --claudeai\n%s", transcript.String()) }()
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

	body, err := sandbox.command(ctx, "/usr/bin/curl", "-fsSL", "--max-time", "8", authorizeURL).CombinedOutput()
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
	// Reading a Unix PTY ends with EIO when the child closes its side.
	_, _ = io.Copy(io.Discard, reader)
	if err := <-waited; err != nil {
		t.Fatalf("Claude login failed: %v", err)
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
	const refreshMargin = 300
	const expiresIn = refreshMargin + 12
	for _, plan := range []string{"max", "pro"} {
		t.Run(plan, func(t *testing.T) {
			limit, expiry := "allowed_warning", expiresIn
			if plan == "pro" {
				limit, expiry = "rejected", 3600
			}
			home := realTempDir(t)
			sandbox := subscriptionSandbox{binary: binary, home: home}
			prepared, err := exec.Command("python3", "subscription_sandbox.py", "prepare", home).CombinedOutput()
			if err != nil {
				t.Fatalf("prepare sandbox: %v\n%s", err, prepared)
			}
			platform := strings.TrimSpace(string(prepared))
			t.Cleanup(func() { _ = os.RemoveAll(platform) })
			outside, err := net.Listen("tcp4", "127.0.0.2:0")
			if err != nil {
				t.Fatal(err)
			}
			hostService := &http.Server{Handler: http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
				response.WriteHeader(http.StatusNoContent)
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
			t.Logf("Host listener %s -> %s", outside.Addr(), response.Status)
			tool := fmt.Sprintf(`/usr/bin/curl --noproxy '*' --max-time 2 -fsS http://%s > direct-result.txt 2>&1; test "$?" -eq 7 && printf stub-tool-ran > tool-result.txt`, outside.Addr())
			startAnthropicStub(t, platform, plan, limit, expiry, tool)
			sandbox.login(t)
			refreshDue := time.Now().Add((expiresIn - refreshMargin) * time.Second)
			run := func(args ...string) ([]byte, error) {
				ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
				defer cancel()
				return sandbox.command(ctx, append([]string{binary}, args...)...).CombinedOutput()
			}
			status, err := run("auth", "status")
			if err != nil {
				t.Fatalf("auth status: %v\n%s", err, status)
			}
			var account struct {
				LoggedIn         bool   `json:"loggedIn"`
				AuthMethod       string `json:"authMethod"`
				SubscriptionType string `json:"subscriptionType"`
			}
			if err := json.Unmarshal(status, &account); err != nil {
				t.Fatalf("decode auth status: %v\n%s", err, status)
			}
			if !account.LoggedIn || account.AuthMethod != "claude.ai" || account.SubscriptionType != plan {
				t.Fatalf("expected a Claude subscription for %s, got %s", plan, status)
			}
			t.Logf("claude auth status\n%s", status)
			if plan == "max" {
				out, err := run("-p", "Write the marker", "--allowedTools", "Bash")
				if err != nil || strings.TrimSpace(string(out)) != "Hi there!" {
					t.Fatalf("expected completed tool turn, got %v\n%s", err, out)
				}
				marker, err := os.ReadFile(filepath.Join(home, "tool-result.txt"))
				if err != nil || string(marker) != "stub-tool-ran" {
					t.Fatalf("expected tool to write its marker, got %q (%v)", marker, err)
				}
				t.Logf("claude -p \"Write the marker\" --allowedTools Bash\n%s", out)
				t.Logf("tool-result.txt: %s", marker)
				direct, err := os.ReadFile(filepath.Join(home, "direct-result.txt"))
				if err != nil || !strings.Contains(string(direct), "Failed to connect to 127.0.0.2") {
					t.Fatalf("expected the Bash tool's direct connection to fail, got %q (%v)", direct, err)
				}
				t.Logf("Bash tool direct connection\n%s", direct)
				// The CLI renews five minutes before expiry. The proxy only
				// returns 429 once it sees the refreshed bearer token.
				time.Sleep(time.Until(refreshDue))
			}
			out, err := run("-p", "say hi")
			if err == nil || !strings.Contains(string(out), "You've hit your 5-hour limit") {
				t.Fatalf("expected subscription limit error, got %v\n%s", err, out)
			}
			t.Logf("claude -p \"say hi\"\n%s", out)
		})
	}
}
