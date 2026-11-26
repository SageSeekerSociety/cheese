//go:build claudee2e

package e2e

import (
	"bufio"
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func startAnthropicStub(t *testing.T, plan, limit string) string {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	cmd := exec.CommandContext(ctx, "node", "../../e2e/stub-anthropic.mjs")
	cmd.Env = append(os.Environ(),
		"STUB_ANTHROPIC_PORT=0",
		"STUB_ANTHROPIC_PLAN="+plan,
		"STUB_ANTHROPIC_RATE_LIMIT="+limit,
		`STUB_ANTHROPIC_TOOL={"name":"Bash","input":{"command":"printf stub-tool-ran > tool-result.txt","description":"Write test marker"}}`,
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
	drained := make(chan struct{})
	t.Cleanup(func() {
		cancel()
		<-drained
		_ = cmd.Wait()
	})
	ready := lines.Scan()
	base, ok := strings.CutPrefix(lines.Text(), "ANTHROPIC_BASE_URL=")
	go func() {
		defer close(drained)
		for lines.Scan() {
			t.Log(lines.Text())
		}
	}()
	if !ready || !ok {
		t.Fatal("Anthropic stub did not announce its listening URL")
	}
	return base
}

func TestClaudeSubscriptionStub(t *testing.T) {
	binary, err := filepath.Abs(claudeBinary(t))
	if err != nil {
		t.Fatal(err)
	}
	version := exec.Command(binary, "--version")
	version.Env = []string{"PATH=" + os.Getenv("PATH")}
	if out, err := version.Output(); err != nil || !strings.HasPrefix(string(out), pinnedVersion+" ") {
		t.Fatalf("expected Claude Code %s, got %q (%v)", pinnedVersion, out, err)
	}
	for _, test := range []struct{ plan, limit string }{
		{plan: "max", limit: "allowed_warning"},
		{plan: "pro", limit: "rejected"},
	} {
		t.Run(test.plan, func(t *testing.T) {
			base := startAnthropicStub(t, test.plan, test.limit)
			home := realTempDir(t)
			config := filepath.Join(home, "claude")
			seed := exec.Command("node", "../../e2e/seed-claude-subscription.mjs")
			seed.Env = []string{"CLAUDE_CONFIG_DIR=" + config, "ANTHROPIC_BASE_URL=" + base}
			if out, err := seed.CombinedOutput(); err != nil {
				t.Fatalf("seed subscription: %v\n%s", err, out)
			}
			run := func(args ...string) ([]byte, error) {
				ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
				defer cancel()
				claude := exec.CommandContext(ctx, binary, args...)
				claude.Dir = home
				claude.Env = []string{
					"HOME=" + home, "PATH=" + os.Getenv("PATH"),
					"CLAUDE_CONFIG_DIR=" + config, "ANTHROPIC_BASE_URL=" + base,
					"NO_PROXY=127.0.0.1", "HTTPS_PROXY=http://127.0.0.1:1", "HTTP_PROXY=http://127.0.0.1:1",
				}
				return claude.CombinedOutput()
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
			if !account.LoggedIn || account.AuthMethod != "claude.ai" || account.SubscriptionType != test.plan {
				t.Fatalf("expected a Claude subscription for %s, got %s", test.plan, status)
			}
			if test.limit == "rejected" {
				out, err := run("-p", "say hi")
				if err == nil || !strings.Contains(string(out), "You've hit your 5-hour limit") {
					t.Fatalf("expected subscription limit error, got %v\n%s", err, out)
				}
				t.Logf("%s", out)
				return
			}
			out, err := run("-p", "Write the marker", "--allowedTools", "Bash")
			if err != nil || strings.TrimSpace(string(out)) != "Hi there!" {
				t.Fatalf("expected completed tool turn, got %v\n%s", err, out)
			}
			marker, err := os.ReadFile(filepath.Join(home, "tool-result.txt"))
			if err != nil || string(marker) != "stub-tool-ran" {
				t.Fatalf("expected tool to write its marker, got %q (%v)", marker, err)
			}
			t.Logf("Tool wrote %q and Claude replied %q", marker, strings.TrimSpace(string(out)))
		})
	}
}
