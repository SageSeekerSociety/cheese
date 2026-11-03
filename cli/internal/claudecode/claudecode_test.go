package claudecode

import (
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

// A fake build: a script that answers `--version` the way Claude Code does.
func fakeBuild(version string) []byte {
	return []byte("#!/bin/sh\necho '" + version + " (Claude Code)'\n")
}

func server(t *testing.T, version string, build []byte, downloads *atomic.Int32) *httptest.Server {
	t.Helper()
	srv, _ := servedPacked(t, version, build, downloads, nil)
	return srv
}

// servedPacked is the platform's route as a machine sees it: the packed copy,
// answering Range requests, with the digest of the build it unpacks to. Each
// Range asked for is sent on ranges.
func servedPacked(t *testing.T, version string, build []byte, downloads *atomic.Int32, ranges chan<- string) (*httptest.Server, []byte) {
	t.Helper()
	var packed bytes.Buffer
	zw := gzip.NewWriter(&packed)
	zw.Write(build)
	zw.Close()
	sum := sha256.Sum256(build)
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
		if ranges != nil {
			ranges <- r.Header.Get("Range")
		}
		w.Header().Set("X-Checksum-SHA256", hex.EncodeToString(sum[:]))
		w.Header().Set("Content-Encoding", "gzip")
		http.ServeContent(w, r, "claude", time.Time{}, bytes.NewReader(packed.Bytes()))
	})
	srv := httptest.NewServer(mux)
	t.Cleanup(srv.Close)
	return srv, packed.Bytes()
}

// The desktop app installs the build in the background while a session's
// bootstrap may be installing it too (`remote_execution/executor_download`):
// whichever comes second waits for the first and uses what it placed.
func TestAnInstallUnderWayElsewhereIsWaitedForNotRepeated(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("the fake build is a shell script")
	}
	home := t.TempDir()
	t.Setenv("HOME", home)
	var downloads atomic.Int32
	srv := server(t, "2.1.299", fakeBuild("2.1.299"), &downloads)
	versions := filepath.Join(home, ".cheese", "claude", "versions")
	os.MkdirAll(versions, 0o700)
	destination := filepath.Join(versions, "2.1.299")
	release, err := lockFile(destination + ".lock")
	if err != nil {
		t.Fatal(err)
	}
	type answer struct {
		path string
		err  error
	}
	done := make(chan answer, 1)
	go func() {
		path, err := Ensure(context.Background(), srv.URL, nil, nil)
		done <- answer{path, err}
	}()
	select {
	case got := <-done:
		t.Fatalf("installed (%v, %v) while another installer held the lock", got.path, got.err)
	case <-time.After(300 * time.Millisecond):
	}
	if err := os.WriteFile(destination, fakeBuild("2.1.299"), 0o700); err != nil {
		t.Fatal(err)
	}
	release()

	got := <-done
	if got.err != nil || got.path != destination {
		t.Fatalf("Ensure = %q, %v; want %q", got.path, got.err, destination)
	}
	if n := downloads.Load(); n != 0 {
		t.Fatalf("downloaded %d times; the build the other installer placed was enough", n)
	}
}

// What an earlier attempt — this command's or a session bootstrap's — fetched
// before it was stopped is continued, not fetched again.
func TestADownloadThatWasCutOffIsContinued(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("the fake build is a shell script")
	}
	home := t.TempDir()
	t.Setenv("HOME", home)
	build := append(fakeBuild("2.1.299"), bytes.Repeat([]byte("# filler\n"), 4096)...)
	var downloads atomic.Int32
	ranges := make(chan string, 4)
	srv, packed := servedPacked(t, "2.1.299", build, &downloads, ranges)
	versions := filepath.Join(home, ".cheese", "claude", "versions")
	os.MkdirAll(versions, 0o700)
	held := len(packed) / 2
	os.WriteFile(filepath.Join(versions, "2.1.299.partial"), packed[:held], 0o600)

	path, err := Ensure(context.Background(), srv.URL, nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	if got, _ := os.ReadFile(path); !bytes.Equal(got, build) {
		t.Fatal("the installed build is not the one served")
	}
	if asked := <-ranges; asked != fmt.Sprintf("bytes=%d-", held) {
		t.Fatalf("asked for %q; want only the rest, from byte %d", asked, held)
	}
	if _, err := os.Stat(filepath.Join(versions, "2.1.299.partial")); !os.IsNotExist(err) {
		t.Errorf("the partial download is still there: %v", err)
	}
}

// The server names the digest of the build it serves; bytes that do not hash
// to it (a proxy that substituted them, a corrupted copy) are never installed.
func TestABuildThatIsNotTheDigestTheServerNamesIsNotInstalled(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("the fake build is a shell script")
	}
	home := t.TempDir()
	t.Setenv("HOME", home)
	build := fakeBuild("2.1.299")
	named := sha256.Sum256([]byte("another build"))
	mux := http.NewServeMux()
	mux.HandleFunc("/connector/claude/pin", func(w http.ResponseWriter, _ *http.Request) {
		w.Write([]byte(`{"version":"2.1.299"}`))
	})
	mux.HandleFunc("/connector/claude/", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Checksum-SHA256", hex.EncodeToString(named[:]))
		w.Write(build)
	})
	srv := httptest.NewServer(mux)
	t.Cleanup(srv.Close)

	if _, err := Ensure(context.Background(), srv.URL, nil, nil); err == nil {
		t.Fatal("a build that is not the one the server names was installed")
	}
	versions := filepath.Join(home, ".cheese", "claude", "versions")
	if _, err := os.Stat(filepath.Join(versions, "2.1.299")); !os.IsNotExist(err) {
		t.Fatal("a build was placed")
	}
}

func TestBytesThatAreNotTheServedCopyAreDiscarded(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("the fake build is a shell script")
	}
	home := t.TempDir()
	t.Setenv("HOME", home)
	var downloads atomic.Int32
	srv, packed := servedPacked(t, "2.1.299", fakeBuild("2.1.299"), &downloads, nil)
	versions := filepath.Join(home, ".cheese", "claude", "versions")
	os.MkdirAll(versions, 0o700)
	// Left by an earlier attempt, but not the start of what is served.
	wrong := append([]byte{0x1f, 0x8b}, bytes.Repeat([]byte("x"), len(packed)/2)...)
	os.WriteFile(filepath.Join(versions, "2.1.299.partial"), wrong, 0o600)

	if _, err := Ensure(context.Background(), srv.URL, nil, nil); err == nil {
		t.Fatal("bytes that are not the served build were installed")
	}
	if _, err := os.Stat(filepath.Join(versions, "2.1.299")); !os.IsNotExist(err) {
		t.Fatal("a build was placed")
	}
	// Thrown away, so the next attempt starts over and succeeds.
	if _, err := Ensure(context.Background(), srv.URL, nil, nil); err != nil {
		t.Fatalf("the attempt after a bad one: %v", err)
	}
}

func TestTheServersBuildIsFetchedOnceAndThenReused(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("the fake build is a shell script")
	}
	t.Setenv("HOME", t.TempDir())
	var downloads atomic.Int32
	srv := server(t, "2.1.299", fakeBuild("2.1.299"), &downloads)
	// The stored base carries the connector path; artifacts are at the origin.
	base := srv.URL + "/connector"

	first, err := Ensure(context.Background(), base, nil, nil)
	if err != nil {
		t.Fatalf("first install: %v", err)
	}
	if !runsAs(first, "2.1.299") {
		t.Fatalf("installed build at %s does not run as the pinned version", first)
	}
	second, err := Ensure(context.Background(), base, nil, nil)
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
	srv := server(t, "2.1.299", fakeBuild("2.0.0"), &downloads)

	if _, err := Ensure(context.Background(), srv.URL, nil, nil); err == nil {
		t.Fatal("a build reporting another version was installed")
	}
	versions := filepath.Join(home, ".cheese", "claude", "versions")
	entries, _ := os.ReadDir(versions)
	for _, entry := range entries {
		// The lock every installer takes stays, as an empty file.
		if entry.Name() == "2.1.299.lock" {
			continue
		}
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
