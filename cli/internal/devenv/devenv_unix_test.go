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
	"strings"
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

// The server's toolchain route, serving body with the digest it claims.
func toolchainServer(t *testing.T, body []byte, claimed string, asked *atomic.Int32) string {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		asked.Add(1)
		if !strings.HasPrefix(r.URL.Path, "/connector/toolchain/python/") {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("X-Checksum-SHA256", claimed)
		w.Write(body)
	}))
	t.Cleanup(srv.Close)
	return srv.URL + "/connector"
}

func digest(body []byte) string {
	sum := sha256.Sum256(body)
	return hex.EncodeToString(sum[:])
}

// A machine whose own python3 exits as the version check would: 0 for new
// enough, 1 for too old.
func machine(t *testing.T, systemPythonOK bool) string {
	t.Helper()
	home := t.TempDir()
	bin := t.TempDir()
	code := "1"
	if systemPythonOK {
		code = "0"
	}
	os.WriteFile(filepath.Join(bin, "python3"), []byte("#!/bin/sh\nexit "+code+"\n"), 0o755)
	t.Setenv("HOME", home)
	t.Setenv("PATH", bin+":/usr/bin:/bin")
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
