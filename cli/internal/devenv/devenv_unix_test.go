//go:build !windows

package devenv

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
)

// A python-build-standalone-shaped archive: everything under `python/`.
func pythonArchive(t *testing.T, entries map[string]string) []byte {
	t.Helper()
	var buf bytes.Buffer
	zipped := gzip.NewWriter(&buf)
	archive := tar.NewWriter(zipped)
	for name, body := range entries {
		if err := archive.WriteHeader(&tar.Header{Name: name, Mode: 0o755, Size: int64(len(body)), Typeflag: tar.TypeReg}); err != nil {
			t.Fatal(err)
		}
		archive.Write([]byte(body))
	}
	archive.Close()
	zipped.Close()
	return buf.Bytes()
}

const placedBwrap = "#!/bin/sh\necho placed bwrap\n"

// The server's toolchain route, serving body as Python with the digest it
// claims, a bwrap, the host tools, and a tmux and a git that say they are the
// placed ones; asked counts the requests for each tool.
func toolchainServer(t *testing.T, body []byte, claimed string, asked *atomic.Int32) string {
	base, _ := toolchainServerCounting(t, body, claimed, asked)
	return base
}

func toolchainServerCounting(t *testing.T, body []byte, claimed string, asked *atomic.Int32) (string, *sync.Map) {
	t.Helper()
	served := map[string][]byte{
		"bubblewrap":   pythonArchive(t, map[string]string{"bubblewrap-0.13.0-cheese.1/bwrap": placedBwrap}),
		"host-tools":   pythonArchive(t, map[string]string{"host-tools-1/bin/ip": "#!/bin/sh\necho placed ip\n"}),
		"tmux":         pythonArchive(t, map[string]string{"tmux-1/bin/tmux": "#!/bin/sh\necho placed tmux\n"}),
		"git":          pythonArchive(t, map[string]string{"git-1/bin/git": "#!/bin/sh\necho placed git\n"}),
		"fuse-runtime": pythonArchive(t, map[string]string{"fuse-2.9.9-cheese.2/libfuse.so.2": "placed libfuse"}),
	}
	var counts sync.Map
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		name := strings.Split(strings.TrimPrefix(r.URL.Path, "/connector/toolchain/"), "/")[0]
		count, _ := counts.LoadOrStore(name, new(atomic.Int32))
		count.(*atomic.Int32).Add(1)
		if name == "python" {
			asked.Add(1)
			w.Header().Set("X-Checksum-SHA256", claimed)
			w.Write(body)
			return
		}
		archive, ok := served[name]
		if !ok {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("X-Checksum-SHA256", digest(archive))
		w.Write(archive)
	}))
	t.Cleanup(srv.Close)
	return srv.URL + "/connector", &counts
}

func askedFor(counts *sync.Map, name string) int32 {
	count, ok := counts.Load(name)
	if !ok {
		return 0
	}
	return count.(*atomic.Int32).Load()
}

func digest(body []byte) string {
	sum := sha256.Sum256(body)
	return hex.EncodeToString(sum[:])
}

// A machine whose own python3 exits as the version check would: 0 for new
// enough, 1 for too old. Its tmux and git are new enough.
func machine(t *testing.T, systemPythonOK bool) string {
	return machineWith(t, systemPythonOK, map[string]string{
		"tmux": "tmux 3.5a",
		"git":  "git version 2.47.3",
	})
}

// A machine with exactly these programs on PATH besides python3, each saying
// the version given; nothing else of the system's is on PATH.
func machineWith(t *testing.T, systemPythonOK bool, programs map[string]string) string {
	t.Helper()
	home := t.TempDir()
	bin := t.TempDir()
	code := "1"
	if systemPythonOK {
		code = "0"
	}
	os.WriteFile(filepath.Join(bin, "python3"), []byte("#!/bin/sh\nexit "+code+"\n"), 0o755)
	for name, says := range programs {
		os.WriteFile(filepath.Join(bin, name), []byte("#!/bin/sh\necho '"+says+"'\n"), 0o755)
	}
	t.Setenv("HOME", home)
	t.Setenv("PATH", bin)
	t.Setenv("CHEESE_RUNTIME_PATH", "")
	return home
}

const placedPython = "#!/bin/sh\necho placed\n"

func TestAMachineWithARecentPythonIsLeftAlone(t *testing.T) {
	machine(t, true)
	var asked atomic.Int32
	base := toolchainServer(t, nil, "", &asked)
	if err := Ensure(context.Background(), base, io.Discard); err != nil {
		t.Fatal(err)
	}
	if asked.Load() != 0 {
		t.Fatal("the server was asked for a Python this machine does not need")
	}
}

func TestAnOldPythonIsReplacedOnPathByTheServersBuild(t *testing.T) {
	home := machine(t, false)
	body := pythonArchive(t, map[string]string{"python/bin/python3": placedPython})
	var asked atomic.Int32
	if err := Ensure(context.Background(), toolchainServer(t, body, digest(body), &asked), io.Discard); err != nil {
		t.Fatal(err)
	}
	out, err := exec.Command("python3").Output()
	if err != nil || strings.TrimSpace(string(out)) != "placed" {
		t.Fatalf("python3 on PATH is not the placed one: %q, %v", out, err)
	}
	placed := filepath.Join(home, ".cheese", "runtime", "python", "bin")
	if !strings.HasPrefix(os.Getenv("PATH"), placed+":") {
		t.Fatalf("PATH: %s", os.Getenv("PATH"))
	}
	// A session's login shell may reorder PATH; it is handed the placed
	// directory apart, to put back in front.
	if !strings.HasPrefix(os.Getenv("CHEESE_RUNTIME_PATH"), placed) {
		t.Fatalf("CHEESE_RUNTIME_PATH: %s", os.Getenv("CHEESE_RUNTIME_PATH"))
	}
}

func TestABuildThatIsNotTheOneThePlatformPinnedIsNotUsed(t *testing.T) {
	home := machine(t, false)
	body := pythonArchive(t, map[string]string{"python/bin/python3": placedPython})
	var asked atomic.Int32
	err := Ensure(context.Background(), toolchainServer(t, body, digest([]byte("another build")), &asked), io.Discard)
	if err == nil {
		t.Fatal("a build with the wrong checksum was accepted")
	}
	if _, err := os.Stat(filepath.Join(home, ".cheese", "runtime", "python", "bin", "python3")); err == nil {
		t.Fatal("a build with the wrong checksum was placed")
	}
}

func TestAnArchiveCannotWriteOutsideItsDirectory(t *testing.T) {
	home := machine(t, false)
	body := pythonArchive(t, map[string]string{
		"python/bin/python3":   placedPython,
		"python/../../escaped": "x",
	})
	var asked atomic.Int32
	if err := Ensure(context.Background(), toolchainServer(t, body, digest(body), &asked), io.Discard); err == nil {
		t.Fatal("an archive reaching outside its directory was unpacked")
	}
	if _, err := os.Stat(filepath.Join(home, ".cheese", "escaped")); err == nil {
		t.Fatal("a file was written outside the runtime directory")
	}
}

func TestOnLinuxThePlatformsBwrapIsPlacedWhateverTheSystemHas(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("bwrap is placed on Linux only")
	}
	home := machine(t, true)
	bin := strings.SplitN(os.Getenv("PATH"), ":", 2)[0]
	os.WriteFile(filepath.Join(bin, "bwrap"), []byte("#!/bin/sh\necho system bwrap\n"), 0o755)
	var asked atomic.Int32
	if err := Ensure(context.Background(), toolchainServer(t, nil, "", &asked), io.Discard); err != nil {
		t.Fatal(err)
	}
	out, err := exec.Command("bwrap").Output()
	if err != nil || strings.TrimSpace(string(out)) != "placed bwrap" {
		t.Fatalf("bwrap on PATH is not the placed one: %q, %v", out, err)
	}
	if !strings.HasPrefix(os.Getenv("PATH"), filepath.Join(home, ".cheese", "runtime", "bubblewrap")+":") {
		t.Fatalf("PATH: %s", os.Getenv("PATH"))
	}
}

func TestElsewhereNoBwrapIsPlaced(t *testing.T) {
	if runtime.GOOS == "linux" {
		t.Skip("bwrap is placed on Linux")
	}
	home := machine(t, true)
	var asked atomic.Int32
	if err := Ensure(context.Background(), toolchainServer(t, nil, "", &asked), io.Discard); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(home, ".cheese", "runtime", "bubblewrap")); err == nil {
		t.Fatal("bwrap was placed on a system that does not run it")
	}
}

func onPath(t *testing.T, program string) string {
	t.Helper()
	out, err := exec.Command(program).Output()
	if err != nil {
		t.Fatalf("%s: %v", program, err)
	}
	return strings.TrimSpace(string(out))
}

func TestOnLinuxATmuxAndGitNewEnoughAreTheSystems(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("tmux and git are placed on Linux only")
	}
	machine(t, true)
	var asked atomic.Int32
	base, counts := toolchainServerCounting(t, nil, "", &asked)
	if err := Ensure(context.Background(), base, io.Discard); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"tmux", "git"} {
		if askedFor(counts, name) != 0 {
			t.Errorf("the server was asked for a %s this machine does not need", name)
		}
	}
	if got := onPath(t, "tmux"); got != "tmux 3.5a" {
		t.Errorf("tmux on PATH: %q", got)
	}
}

func TestOnLinuxAMissingTmuxAndATooOldGitAreTheServersBuilds(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("tmux and git are placed on Linux only")
	}
	// git 2.30 lacks `rev-parse --path-format`, which a room's checkout asks.
	machineWith(t, true, map[string]string{"git": "git version 2.30.9"})
	var asked atomic.Int32
	if err := Ensure(context.Background(), toolchainServer(t, nil, "", &asked), io.Discard); err != nil {
		t.Fatal(err)
	}
	if got := onPath(t, "tmux"); got != "placed tmux" {
		t.Errorf("tmux on PATH: %q", got)
	}
	if got := onPath(t, "git"); got != "placed git" {
		t.Errorf("git on PATH: %q", got)
	}
}

func TestOnLinuxAPlacedGitStaysTheOneUsedOnceTheSystemHasOne(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("git is placed on Linux only")
	}
	machineWith(t, true, map[string]string{"tmux": "tmux 3.5a"})
	var asked atomic.Int32
	base := toolchainServer(t, nil, "", &asked)
	if err := Ensure(context.Background(), base, io.Discard); err != nil {
		t.Fatal(err)
	}
	// The system gains a git; the next start keeps the placed one.
	bin := t.TempDir()
	os.WriteFile(filepath.Join(bin, "git"), []byte("#!/bin/sh\necho 'git version 2.50.0'\n"), 0o755)
	t.Setenv("PATH", bin+":"+os.Getenv("PATH"))
	if err := Ensure(context.Background(), base, io.Discard); err != nil {
		t.Fatal(err)
	}
	if got := onPath(t, "git"); got != "placed git" {
		t.Errorf("git on PATH: %q", got)
	}
}

func TestOnLinuxTheHostToolsArePlacedButNotOnPath(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("the host tools are placed on Linux only")
	}
	home := machine(t, true)
	var asked atomic.Int32
	if err := Ensure(context.Background(), toolchainServer(t, nil, "", &asked), io.Discard); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(home, ".cheese", "runtime", "host-tools", "bin", "ip")); err != nil {
		t.Fatalf("the host tools were not placed: %v", err)
	}
	if _, err := exec.LookPath("ip"); err == nil {
		t.Fatal("the host tools are on the connector's PATH")
	}
}

func TestOnLinuxTheFuseRuntimeIsPlacedWhereTheExecutorLooksForIt(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("the fuse runtime is placed on Linux only")
	}
	home := machine(t, true)
	var asked atomic.Int32
	if err := Ensure(context.Background(), toolchainServer(t, nil, "", &asked), io.Discard); err != nil {
		t.Fatal(err)
	}
	// The archive's own top directory is dropped, so the file lands directly
	// inside — the path `bootstrap.placed_libfuse` reads.
	placed := filepath.Join(home, ".cheese", "runtime", "fuse-runtime", "libfuse.so.2")
	body, err := os.ReadFile(placed)
	if err != nil {
		t.Fatalf("the library was not placed at %s: %v", placed, err)
	}
	if string(body) != "placed libfuse" {
		t.Fatalf("placed file is %q", body)
	}
	// It is not a program: putting it on PATH would be a file nothing runs.
	if strings.HasPrefix(os.Getenv("PATH"), filepath.Join(home, ".cheese", "runtime", "fuse-runtime")+":") {
		t.Fatal("the fuse runtime is on the connector's PATH")
	}
}

func TestOnLinuxAMissingFuseRuntimeDoesNotFailTheConnect(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("the fuse runtime is placed on Linux only")
	}
	// Everything else is served; only the fuse runtime answers 404, which is
	// what a transient outage on the toolchain route or a release not fetched
	// yet looks like. `Ensure`'s error is fatal in `cheesehost connect`
	// (`daemoncmd.model_login`), so this artifact must not produce one: the
	// machine runs without a transcript view rather than without a connector.
	machine(t, true)
	// Everything else is served, so the only failure is the optional one.
	base := toolchainServerWithout(t, "fuse-runtime")
	if err := Ensure(context.Background(), base, io.Discard); err != nil {
		t.Fatalf("an unfetchable fuse runtime failed the whole Ensure: %v", err)
	}
}

func toolchainServerWithout(t *testing.T, absent string) string {
	t.Helper()
	served := map[string][]byte{
		"bubblewrap": pythonArchive(t, map[string]string{"bubblewrap-0.13.0-cheese.1/bwrap": placedBwrap}),
		"host-tools": pythonArchive(t, map[string]string{"host-tools-1/bin/ip": "#!/bin/sh\necho placed ip\n"}),
		"tmux":       pythonArchive(t, map[string]string{"tmux-1/bin/tmux": "#!/bin/sh\necho placed tmux\n"}),
		"git":        pythonArchive(t, map[string]string{"git-1/bin/git": "#!/bin/sh\necho placed git\n"}),
	}
	delete(served, absent)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		name := strings.Split(strings.TrimPrefix(r.URL.Path, "/connector/toolchain/"), "/")[0]
		archive, ok := served[name]
		if !ok {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("X-Checksum-SHA256", digest(archive))
		w.Write(archive)
	}))
	t.Cleanup(srv.Close)
	return srv.URL + "/connector"
}

func TestOnLinuxWhatWasPlacedIsUsedWhileTheServerCannotBeReached(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("tmux is placed on Linux only")
	}
	machineWith(t, true, map[string]string{"git": "git version 2.47.3"})
	var asked atomic.Int32
	if err := Ensure(context.Background(), toolchainServer(t, nil, "", &asked), io.Discard); err != nil {
		t.Fatal(err)
	}
	// The machine starts again before its network: the connector's screens
	// still need the tmux it placed.
	t.Setenv("PATH", strings.SplitN(os.Getenv("PATH"), ":", 3)[2])
	t.Setenv("CHEESE_RUNTIME_PATH", "")
	if err := Ensure(context.Background(), "http://127.0.0.1:1/connector", io.Discard); err == nil {
		t.Fatal("an unreachable server was not reported")
	}
	if got := onPath(t, "tmux"); got != "placed tmux" {
		t.Errorf("tmux on PATH: %q", got)
	}
}

func TestOnLinuxTheOldestTmuxAndGitTheConnectorTakesAreTheSystems(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("tmux and git are placed on Linux only")
	}
	machineWith(t, true, map[string]string{"tmux": "tmux 3.3", "git": "git version 2.31.0"})
	var asked atomic.Int32
	base, counts := toolchainServerCounting(t, nil, "", &asked)
	if err := Ensure(context.Background(), base, io.Discard); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"tmux", "git"} {
		if askedFor(counts, name) != 0 {
			t.Errorf("the server was asked for a %s this machine does not need", name)
		}
	}
}
