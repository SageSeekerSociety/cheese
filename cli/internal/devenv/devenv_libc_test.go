//go:build !windows

package devenv

import (
	"bytes"
	"context"
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

// The toolchain route with a Python for each C library: the glibc build under
// the plain platform name and the musl build under its "-musl" name, each
// saying which it is, and the other Linux tools under the plain names only.
// requested records the platform each tool was asked for.
func libcServer(t *testing.T, requested *sync.Map) string {
	t.Helper()
	gnu := pythonArchive(t, map[string]string{"python/bin/python3": "#!/bin/sh\necho placed gnu\n"})
	musl := pythonArchive(t, map[string]string{"python/bin/python3": "#!/bin/sh\necho placed musl\n"})
	others := map[string][]byte{
		"bubblewrap":   pythonArchive(t, map[string]string{"bubblewrap-0.13.0-cheese.1/bwrap": placedBwrap}),
		"host-tools":   pythonArchive(t, map[string]string{"host-tools-1/bin/ip": "#!/bin/sh\necho placed ip\n"}),
		"fuse-runtime": pythonArchive(t, map[string]string{"fuse-2.9.9-cheese.2/libfuse.so.2": "placed libfuse"}),
	}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		parts := strings.Split(strings.TrimPrefix(r.URL.Path, "/connector/toolchain/"), "/")
		name, platform := parts[0], parts[1]
		requested.Store(name, platform)
		var archive []byte
		switch {
		case name == "python" && strings.HasSuffix(platform, "-musl"):
			archive = musl
		case name == "python":
			archive = gnu
		case !strings.HasSuffix(platform, "-musl"):
			archive = others[name]
		}
		if archive == nil {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("X-Checksum-SHA256", digest(archive))
		w.Write(archive)
	}))
	t.Cleanup(srv.Close)
	return srv.URL + "/connector"
}

// A musl machine is given the musl Python: the glibc one is there once placed
// but cannot start, its loader being absent. The tools beside it are static
// builds and are asked for under the names they always were.
func TestOnAMuslMachineThePlatformsPythonIsTheMuslBuild(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("the C library is chosen on Linux only")
	}
	home := machine(t, true)
	saved := muslHost
	muslHost = func() bool { return true }
	t.Cleanup(func() { muslHost = saved })
	var requested sync.Map
	if err := Ensure(context.Background(), libcServer(t, &requested), os.Stderr); err != nil {
		t.Fatal(err)
	}
	asked, _ := requested.Load("python")
	if want := map[string]string{"amd64": "linux-x64-musl", "arm64": "linux-arm64-musl"}[runtime.GOARCH]; asked != want {
		t.Errorf("the Python was asked for as %v, want %s", asked, want)
	}
	placed := filepath.Join(home, ".cheese", "runtime", "python", "bin", "python3")
	if got := os.Getenv("CHEESE_PYTHON"); got != placed {
		t.Fatalf("CHEESE_PYTHON: %q, want %q", got, placed)
	}
	if out, _ := exec.Command(placed).Output(); strings.TrimSpace(string(out)) != "placed musl" {
		t.Errorf("the Python placed says %q, want the musl build", out)
	}
	if asked, _ := requested.Load("bubblewrap"); strings.HasSuffix(asked.(string), "-musl") {
		t.Errorf("bwrap was asked for as %v", asked)
	}
}

// A Python that is placed but cannot start — the case of a build linked for a
// C library the machine lacks — names no Python, so a launch refuses for want
// of one, and the connector says which file did not start.
func TestAPlacedPythonThatCannotStartIsNamedNowhere(t *testing.T) {
	home := machine(t, true)
	t.Setenv("CHEESE_PYTHON", "/somewhere/else/python3")
	body := pythonArchive(t, map[string]string{"python/bin/python3": "#!/nonexistent/loader\n"})
	var asked atomic.Int32
	err := Ensure(context.Background(), toolchainServer(t, body, digest(body), &asked), &bytes.Buffer{})
	placed := filepath.Join(home, ".cheese", "runtime", "python", "bin", "python3")
	if _, statErr := os.Stat(placed); statErr != nil {
		t.Fatalf("the Python was not placed at all: %v", statErr)
	}
	if got, set := os.LookupEnv("CHEESE_PYTHON"); set {
		t.Errorf("CHEESE_PYTHON names %q, which does not start", got)
	}
	if err == nil || !strings.Contains(err.Error(), placed) {
		t.Errorf("Ensure did not say that %s does not start: %v", placed, err)
	}
}
