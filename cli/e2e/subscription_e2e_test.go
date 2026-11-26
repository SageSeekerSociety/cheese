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
	"strconv"
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

func startAnthropicStub(t *testing.T, socket, meter string, expiresIn int, toolCommand string) anthropicStub {
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
		"STUB_ANTHROPIC_SOCKET="+socket,
		"STUB_ANTHROPIC_PORT=0",
		"STUB_ANTHROPIC_PLAN=max",
		"STUB_ANTHROPIC_METER="+meter,
		"STUB_ANTHROPIC_RATE_LIMIT=allowed",
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
	var stub anthropicStub
	if lines.Scan() {
		prefix := "STUB_ANTHROPIC_SOCKET="
		if socket == "" {
			prefix = "HTTPS_PROXY="
		}
		stub.address, _ = strings.CutPrefix(lines.Text(), prefix)
	}
	if lines.Scan() {
		stub.ca, _ = strings.CutPrefix(lines.Text(), "NODE_EXTRA_CA_CERTS=")
	}
	drained := make(chan struct{})
	var gaps []string
	go func() {
		defer close(drained)
		for lines.Scan() {
			line := lines.Text()
			t.Log(line)
			if meter == "null" {
				continue
			}
			_, outcome, found := strings.Cut(line, " -> ")
			fields := strings.Fields(outcome)
			if !found || len(fields) != 2 || fields[1] == "(upstream)" {
				continue
			}
			if fields[1] != "(table)" || !strings.HasPrefix(fields[0], "2") {
				gaps = append(gaps, line)
			}
		}
	}()
	t.Cleanup(func() {
		cancel()
		<-drained
		_ = cmd.Wait()
		if len(gaps) > 0 {
			t.Errorf("Metered control requests without a 2xx table answer:\n%s", strings.Join(gaps, "\n"))
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

func loginOnHost(t *testing.T, binary, home string, stub anthropicStub) {
	t.Helper()
	owner := filepath.Join(home, "owner")
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
	cmd := exec.CommandContext(ctx, cheesehostBinary(t), "claude", "login", "--login", place.FirstClaudeLogin, "--config", cfg)
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

func runSubscription(t *testing.T, caseName, binary, home, action, prompt string) (string, error) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 25*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, "python3", "subscription_sandbox.py", "run", home, caseName, binary, action)
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
	const refreshMargin = 300
	const expiresIn = refreshMargin + 12
	for _, caseName := range []string{"metered", "owned"} {
		t.Run(caseName, func(t *testing.T) {
			home := realTempDir(t)
			prepared, err := exec.Command("python3", "subscription_sandbox.py", "prepare", home).CombinedOutput()
			if err != nil {
				t.Fatalf("prepare sandbox: %v\n%s", err, prepared)
			}
			var paths struct{ Platform, Sockets string }
			if err := json.Unmarshal(prepared, &paths); err != nil {
				t.Fatalf("read sandbox paths: %v\n%s", err, prepared)
			}
			platform := paths.Platform
			t.Cleanup(func() {
				_ = os.RemoveAll(platform)
				_ = os.RemoveAll(paths.Sockets)
			})

			// A successful probe on the host establishes what the tool cannot dial.
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
			tool := fmt.Sprintf(`/usr/bin/curl --noproxy '*' --max-time 2 -fsS http://%s > direct-result.txt 2>&1; test "$?" -eq 7 && printf stub-tool-ran > tool-result.txt`, outside.Addr())
			meter := "null"
			if caseName == "metered" {
				placeholder := stageProduction(t, python, "placeholder")
				payload, err := json.Marshal(map[string]string{
					"placeholder": placeholder, "project": tableProject, "topic": tableTopic,
				})
				if err != nil {
					t.Fatal(err)
				}
				meter = string(payload)
			}
			stub := startAnthropicStub(t, filepath.Join(platform, "forge.sock"), meter, expiresIn, tool)
			pem, err := os.ReadFile(stub.ca)
			if err != nil {
				t.Fatal(err)
			}
			for _, name := range []string{"ca.pem", "ca-bundle.pem"} {
				if err := os.WriteFile(filepath.Join(platform, name), pem, 0o644); err != nil {
					t.Fatal(err)
				}
			}
			stageProduction(t, python, "stage", home, caseName, stub.ca, tableProject, tableTopic)
			var refreshDue time.Time
			if caseName == "owned" {
				hostStub := startAnthropicStub(t, "", "null", expiresIn, tool)
				loginOnHost(t, binary, home, hostStub)
				refreshDue = time.Now().Add((expiresIn - refreshMargin) * time.Second)
				status, err := runSubscription(t, caseName, binary, home, "status", "")
				var account struct {
					LoggedIn         bool   `json:"loggedIn"`
					AuthMethod       string `json:"authMethod"`
					SubscriptionType string `json:"subscriptionType"`
				}
				if err != nil || json.Unmarshal([]byte(status), &account) != nil ||
					!account.LoggedIn || account.AuthMethod != "claude.ai" || account.SubscriptionType != "max" {
					t.Fatalf("sandbox did not pick up the host's subscription: %v\n%s", err, status)
				}
			}
			out, err := runSubscription(t, caseName, binary, home, "turn", "Write the marker")
			if err != nil || !strings.Contains(out, `"result":"Hi there!"`) {
				t.Fatalf("expected completed tool turn: %v\n%s", err, out)
			}
			workspace := filepath.Join(home, "workspace")
			marker, err := os.ReadFile(filepath.Join(workspace, "tool-result.txt"))
			if err != nil || string(marker) != "stub-tool-ran" {
				t.Fatalf("expected tool to write its marker, got %q (%v)", marker, err)
			}
			direct, err := os.ReadFile(filepath.Join(workspace, "direct-result.txt"))
			if err != nil || !strings.Contains(string(direct), "Failed to connect to 127.0.0.2") {
				t.Fatalf("expected the Bash tool's direct connection to fail, got %q (%v)", direct, err)
			}
			t.Logf("Host listener %s -> %s; tool inside sandbox:\n%s", outside.Addr(), response.Status, direct)
			if caseName == "owned" {
				time.Sleep(time.Until(refreshDue))
				out, _ := runSubscription(t, caseName, binary, home, "turn", "say hi")
				if !strings.Contains(out, "You've hit your 5-hour limit") {
					t.Fatalf("expected subscription limit after refresh\n%s", out)
				}
			}
		})
	}
}
