package update

import (
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestPlatformDir(t *testing.T) {
	cases := []struct {
		goos, goarch, want string
	}{
		{"linux", "amd64", "linux-amd64"},
		{"linux", "arm64", "linux-arm64"},
		{"darwin", "amd64", "darwin-amd64"},
		{"darwin", "arm64", "darwin-arm64"},
		{"windows", "amd64", "windows-amd64"},
		{"windows", "arm64", "windows-arm64"},
	}
	for _, c := range cases {
		got, err := platformDir(c.goos, c.goarch)
		if err != nil {
			t.Fatalf("platformDir(%q,%q) error: %v", c.goos, c.goarch, err)
		}
		if got != c.want {
			t.Errorf("platformDir(%q,%q) = %q, want %q", c.goos, c.goarch, got, c.want)
		}
	}
}

func TestPlatformDirUnsupported(t *testing.T) {
	if _, err := platformDir("freebsd", "amd64"); err == nil {
		t.Error("expected error for freebsd")
	}
	if _, err := platformDir("linux", "riscv64"); err == nil {
		t.Error("expected error for riscv64")
	}
}

func TestBinaryURL(t *testing.T) {
	// The artifact is named cheesehost — the only name the server serves
	// (installer.py's `/latest/{target}/cheesehost`); `.../cheese` is a 404.
	// Dirs are Go-style (platformDir's output), which is also all the server's
	// target validation accepts — never uname-style linux-x86_64.
	cases := []struct {
		base, dir, want string
	}{
		{
			"https://cheese.ruc.edu.cn/api", "linux-amd64",
			"https://cheese.ruc.edu.cn/connector/latest/linux-amd64/cheesehost",
		},
		{
			"http://127.0.0.1:8080", "darwin-arm64",
			"http://127.0.0.1:8080/connector/latest/darwin-arm64/cheesehost",
		},
		{
			"https://example.com/", "linux-arm64",
			"https://example.com/connector/latest/linux-arm64/cheesehost",
		},
		{
			"https://example.com/connector", "windows-amd64",
			"https://example.com/connector/latest/windows-amd64/cheesehost.exe",
		},
	}
	for _, c := range cases {
		got, err := binaryURL(c.base, c.dir)
		if err != nil {
			t.Fatalf("binaryURL(%q,%q) error: %v", c.base, c.dir, err)
		}
		if got != c.want {
			t.Errorf("binaryURL(%q,%q) = %q, want %q", c.base, c.dir, got, c.want)
		}
	}
}

func TestBinaryURLBadBase(t *testing.T) {
	if _, err := binaryURL("not-a-url", "linux-amd64"); err == nil {
		t.Error("expected error for base without host")
	}
}

// The digest a connector announces must be the digest of the very bytes on
// disk — that identity is what lets the server decide the machine is running
// something other than what it serves, and an update installs a build by
// renaming exactly those bytes into place.
func TestSelfDigestIsTheHashOfTheRunningBinary(t *testing.T) {
	got, err := SelfDigest()
	if err != nil {
		t.Fatalf("SelfDigest error: %v", err)
	}
	self, err := SelfPath()
	if err != nil {
		t.Fatalf("SelfPath error: %v", err)
	}
	raw, err := os.ReadFile(self)
	if err != nil {
		t.Fatalf("read %s: %v", self, err)
	}
	want := fmt.Sprintf("%x", sha256.Sum256(raw))
	if got != want {
		t.Errorf("SelfDigest() = %q, want %q", got, want)
	}
	// Stable: two calls on an unchanged binary must not disagree, or every
	// reconnect would look like a different build and force an update.
	again, err := SelfDigest()
	if err != nil || again != got {
		t.Errorf("SelfDigest() second call = %q (err %v), want %q", again, err, got)
	}
}

// An update request means "take what the origin publishes". When the origin
// publishes the bytes this process already runs there is nothing to hand off
// to — and handing off anyway drops the machine's link on every reconnect.
func TestFetchOfTheRunningBuildIsNotAnUpdate(t *testing.T) {
	self, err := SelfPath()
	if err != nil {
		t.Fatalf("SelfPath error: %v", err)
	}
	running, err := os.ReadFile(self)
	if err != nil {
		t.Fatalf("read %s: %v", self, err)
	}
	served := running
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write(served)
	}))
	defer origin.Close()
	leftovers := func() []string {
		found, _ := filepath.Glob(filepath.Join(filepath.Dir(self), ".cheese-update-*"))
		return found
	}

	tmp, err := Fetch(context.Background(), origin.URL)
	if !errors.Is(err, ErrCurrent) {
		t.Fatalf("Fetch of the running build = (%q, %v), want ErrCurrent", tmp, err)
	}
	if found := leftovers(); len(found) != 0 {
		t.Errorf("Fetch left downloads behind: %v", found)
	}

	// Different bytes are a real update: never ErrCurrent. These fail the
	// runnable check, which is the other half of Fetch's contract.
	served = append([]byte("not this build"), running[:64]...)
	if _, err := Fetch(context.Background(), origin.URL); err == nil || errors.Is(err, ErrCurrent) {
		t.Fatalf("Fetch of different bytes = %v, want a verification failure", err)
	}
	if found := leftovers(); len(found) != 0 {
		t.Errorf("Fetch left downloads behind: %v", found)
	}
}

// A download that stops arriving is given up soon; one that keeps arriving,
// however slowly, is not cut off by a fixed total.
func TestFetchGivesUpOnAStalledDownloadButNotASlowOne(t *testing.T) {
	saved := stallAfter
	stallAfter = 300 * time.Millisecond
	defer func() { stallAfter = saved }()
	self, err := SelfPath()
	if err != nil {
		t.Fatalf("SelfPath error: %v", err)
	}
	leftovers := func() []string {
		found, _ := filepath.Glob(filepath.Join(filepath.Dir(self), ".cheese-update-*"))
		return found
	}

	release := make(chan struct{})
	stalls := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte("first part of a binary"))
		w.(http.Flusher).Flush()
		<-release
	}))
	defer stalls.Close()
	defer close(release)
	began := time.Now()
	_, err = Fetch(context.Background(), stalls.URL)
	if err == nil || !strings.Contains(err.Error(), "stalled") {
		t.Fatalf("Fetch of a stalled download = %v, want a stall", err)
	}
	if took := time.Since(began); took > 5*time.Second {
		t.Errorf("a stalled download took %s to be given up", took)
	}

	trickles := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		for i := 0; i < 12; i++ {
			_, _ = w.Write([]byte("chunk "))
			w.(http.Flusher).Flush()
			time.Sleep(100 * time.Millisecond)
		}
	}))
	defer trickles.Close()
	_, err = Fetch(context.Background(), trickles.URL)
	if err == nil || strings.Contains(err.Error(), "stalled") || errors.Is(err, ErrCurrent) {
		t.Fatalf("Fetch of a slow download = %v, want it downloaded and then refused as not runnable", err)
	}
	if found := leftovers(); len(found) != 0 {
		t.Errorf("Fetch left downloads behind: %v", found)
	}
}

// installDir is a directory holding a stand-in for the running executable and
// a finished download of a build, as Fetch leaves it for Replace.
func installDir(t *testing.T, build []byte) (self, tmp string) {
	t.Helper()
	dir := t.TempDir()
	self = filepath.Join(dir, "cheesehost.exe")
	if err := os.WriteFile(self, []byte("the running build"), 0o755); err != nil {
		t.Fatal(err)
	}
	tmp = filepath.Join(dir, ".cheese-update-1.exe")
	if err := os.WriteFile(tmp, build, 0o755); err != nil {
		t.Fatal(err)
	}
	return self, tmp
}

// windowsReplaceBlocked runs Windows' replace for the rest of the test, with
// the binary an earlier update moved aside still held by a running process.
// What Windows refuses there — deleting that file, or renaming onto it — is
// what any OS refuses for a non-empty directory, so one stands in for it.
func windowsReplaceBlocked(t *testing.T, self string) {
	t.Helper()
	saved := replace
	replace = replaceAside
	t.Cleanup(func() { replace = saved })
	if err := os.MkdirAll(filepath.Join(self+".old", "held"), 0o755); err != nil {
		t.Fatal(err)
	}
}

func forgetFailedBuild(t *testing.T) {
	t.Helper()
	failed.hold("")
	t.Cleanup(func() { failed.hold("") })
}

func downloadsBeside(self string) []string {
	found, _ := filepath.Glob(filepath.Join(filepath.Dir(self), ".cheese-update-*"))
	return found
}

// A replace that fails keeps the running build and takes nothing more from the
// disk: on Windows the download was left behind on every failed attempt.
func TestFailedWindowsReplaceRemovesTheDownload(t *testing.T) {
	forgetFailedBuild(t)
	self, tmp := installDir(t, []byte("the new build"))
	windowsReplaceBlocked(t, self)

	if err := Replace(tmp, self); err == nil {
		t.Fatal("Replace with the moved-aside binary still held = nil, want an error")
	}
	if found := downloadsBeside(self); len(found) != 0 {
		t.Errorf("a failed replace left the download behind: %v", found)
	}
	if got, _ := os.ReadFile(self); string(got) != "the running build" {
		t.Errorf("a failed replace changed the executable to %q", got)
	}
}

// After a build fails to install, being told to update again — which the
// server does on every reconnect — does not download that build again, while a
// newer build is still fetched, and the failed one is tried again once the hold
// has passed.
func TestBuildThatFailedToInstallIsNotDownloadedAgain(t *testing.T) {
	forgetFailedBuild(t)
	build := []byte("a build that cannot be swapped in")
	self, tmp := installDir(t, build)
	windowsReplaceBlocked(t, self)
	if err := Replace(tmp, self); err == nil {
		t.Fatal("Replace with the moved-aside binary still held = nil, want an error")
	}

	var mu sync.Mutex
	served := build
	serve := func(b []byte) { mu.Lock(); served = b; mu.Unlock() }
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		mu.Lock()
		body := served
		mu.Unlock()
		// The origin names the build it serves, as installer.py does.
		w.Header().Set("X-Checksum-SHA256", fmt.Sprintf("%x", sha256.Sum256(body)))
		_, _ = w.Write(body)
	}))
	defer origin.Close()
	running, _ := SelfPath()

	tmp, err := Fetch(context.Background(), origin.URL)
	if !errors.Is(err, ErrHeld) {
		t.Fatalf("Fetch of the build that just failed = (%q, %v), want ErrHeld", tmp, err)
	}
	if found := downloadsBeside(running); len(found) != 0 {
		t.Errorf("a held build left a download behind: %v", found)
	}

	// A different build is a new chance: downloaded (and here refused as not
	// runnable, which is as far as these bytes can go).
	serve([]byte("a newer build"))
	if _, err := Fetch(context.Background(), origin.URL); err == nil || errors.Is(err, ErrHeld) {
		t.Fatalf("Fetch of a newer build = %v, want it downloaded and refused as not runnable", err)
	}

	saved := failedHold
	failedHold = 0
	defer func() { failedHold = saved }()
	serve(build)
	if _, err := Fetch(context.Background(), origin.URL); err == nil || errors.Is(err, ErrHeld) {
		t.Fatalf("Fetch after the hold = %v, want the build downloaded again", err)
	}
	if found := downloadsBeside(running); len(found) != 0 {
		t.Errorf("Fetch left downloads behind: %v", found)
	}
}

// What earlier runs left beside the executable is cleared at start, and only
// that: a download younger than an hour may be one another process is still
// writing or about to swap in.
func TestCleanupRemovesAbandonedDownloadsOnly(t *testing.T) {
	dir := t.TempDir()
	self := filepath.Join(dir, "cheesehost.exe")
	write := func(name string, size int, age time.Duration) string {
		path := filepath.Join(dir, name)
		if err := os.WriteFile(path, make([]byte, size), 0o755); err != nil {
			t.Fatal(err)
		}
		when := time.Now().Add(-age)
		if err := os.Chtimes(path, when, when); err != nil {
			t.Fatal(err)
		}
		return path
	}
	write("cheesehost.exe", 10, 0)
	abandoned := []string{
		write(".cheese-update-111.exe", 1000, 72*time.Hour),
		write(".cheese-update-222.exe", 2000, 2*time.Hour),
	}
	recent := write(".cheese-update-333.exe", 3000, time.Minute)
	other := write("notes.txt", 10, 72*time.Hour)

	removed, size := Cleanup(self)

	if removed != 2 || size != 3000 {
		t.Errorf("Cleanup = (%d, %d), want (2, 3000)", removed, size)
	}
	for _, path := range abandoned {
		if _, err := os.Stat(path); !errors.Is(err, os.ErrNotExist) {
			t.Errorf("abandoned download %s is still there", filepath.Base(path))
		}
	}
	for _, path := range []string{self, recent, other} {
		if _, err := os.Stat(path); err != nil {
			t.Errorf("Cleanup removed %s, which it must keep: %v", filepath.Base(path), err)
		}
	}
}
