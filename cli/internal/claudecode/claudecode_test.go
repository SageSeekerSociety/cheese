package claudecode

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync/atomic"
	"testing"
)

// A fake build: a script that answers `--version` the way Claude Code does.
func fakeBuild(version string) []byte {
	return []byte("#!/bin/sh\necho '" + version + " (Claude Code)'\n")
}

func server(t *testing.T, version string, build []byte, downloads *atomic.Int32) *httptest.Server {
	t.Helper()
	mux := http.NewServeMux()
	mux.HandleFunc("/connector/claude/pin", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"version":"` + version + `"}`))
	})
	mux.HandleFunc("/connector/claude/", func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasPrefix(r.URL.Path, "/connector/claude/"+version+"/") {
			http.NotFound(w, r)
			return
		}
		downloads.Add(1)
		w.Write(build)
	})
	srv := httptest.NewServer(mux)
	t.Cleanup(srv.Close)
	return srv
}

func TestTheServersBuildIsFetchedOnceAndThenReused(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("the fake build is a shell script")
	}
	t.Setenv("HOME", t.TempDir())
	var downloads atomic.Int32
	srv := server(t, "2.1.282", fakeBuild("2.1.282"), &downloads)
	// The stored base carries the connector path; artifacts are at the origin.
	base := srv.URL + "/connector"

	first, err := Ensure(context.Background(), base, nil)
	if err != nil {
		t.Fatalf("first install: %v", err)
	}
	if !runsAs(first, "2.1.282") {
		t.Fatalf("installed build at %s does not run as the pinned version", first)
	}
	second, err := Ensure(context.Background(), base, nil)
	if err != nil {
		t.Fatalf("second install: %v", err)
	}
	if second != first {
		t.Fatalf("second answer %s, first %s", second, first)
	}
	if got := downloads.Load(); got != 1 {
		t.Fatalf("downloaded %d times, want once", got)
	}
}

func TestABuildThatIsNotThePinnedVersionIsRefused(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("the fake build is a shell script")
	}
	home := t.TempDir()
	t.Setenv("HOME", home)
	var downloads atomic.Int32
	srv := server(t, "2.1.282", fakeBuild("2.0.0"), &downloads)

	if _, err := Ensure(context.Background(), srv.URL, nil); err == nil {
		t.Fatal("a build reporting another version was installed")
	}
	versions := filepath.Join(home, ".cheese", "claude", "versions")
	entries, _ := os.ReadDir(versions)
	for _, entry := range entries {
		t.Errorf("left %s behind in %s", entry.Name(), versions)
	}
}

func TestThePlatformsLoginIsTheOnlyLoginClaudeCodeSees(t *testing.T) {
	env := Environment([]string{
		"PATH=/usr/bin",
		"CLAUDE_CONFIG_DIR=/home/owner/.claude",
		"CLAUDE_SECURESTORAGE_CONFIG_DIR=/home/owner/.claude",
		"CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-someone-elses",
		"USER=owner",
	}, "/home/owner/.cheese/claude-login")

	got := map[string][]string{}
	for _, entry := range env {
		name, value, _ := strings.Cut(entry, "=")
		got[name] = append(got[name], value)
	}
	if v := got["CLAUDE_CONFIG_DIR"]; len(v) != 1 || v[0] != "/home/owner/.cheese/claude-login" {
		t.Fatalf("CLAUDE_CONFIG_DIR = %v, want only the platform's login directory", v)
	}
	for _, name := range []string{"CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_SECURESTORAGE_CONFIG_DIR"} {
		if v, ok := got[name]; ok {
			t.Errorf("%s=%v reaches Claude Code and would override the platform's login", name, v)
		}
	}
	if v := got["PATH"]; len(v) != 1 || v[0] != "/usr/bin" {
		t.Errorf("PATH = %v, the rest of the environment is kept", v)
	}
}

func TestAServiceEnvironmentWithoutUserStillNamesTheUser(t *testing.T) {
	env := Environment([]string{"PATH=/usr/bin"}, "/x")
	for _, entry := range env {
		if strings.HasPrefix(entry, "USER=") && len(entry) > len("USER=") {
			return
		}
	}
	t.Fatal("no USER in the environment; Claude Code cannot find a Keychain login on macOS without it")
}

func TestEachMachineAsksForTheBuildPublishedForIt(t *testing.T) {
	cases := []struct {
		goos, goarch string
		musl         bool
		want         string
	}{
		{"linux", "amd64", false, "linux-x64"},
		{"linux", "arm64", true, "linux-arm64-musl"},
		{"darwin", "arm64", false, "darwin-arm64"},
		{"darwin", "amd64", false, "darwin-x64"},
		{"windows", "amd64", false, "win32-x64"},
	}
	for _, c := range cases {
		got, err := vendorPlatform(c.goos, c.goarch, c.musl)
		if err != nil || got != c.want {
			t.Errorf("%s/%s musl=%v: got %q, %v; want %q", c.goos, c.goarch, c.musl, got, err, c.want)
		}
	}
	if _, err := vendorPlatform("freebsd", "amd64", false); err == nil {
		t.Error("a system with no published build was given one")
	}
}
