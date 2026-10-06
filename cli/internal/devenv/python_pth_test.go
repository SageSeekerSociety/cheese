package devenv

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func runtimeDir(t *testing.T, files ...string) string {
	t.Helper()
	dir := t.TempDir()
	for _, name := range files {
		if err := os.WriteFile(filepath.Join(dir, name), []byte("x"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	return dir
}

func TestTheRuntimeBuildsItsPathFromItsOwnFilesOnly(t *testing.T) {
	// The distribution ships a ._pth without site; an earlier connector deleted it.
	dir := runtimeDir(t, "python3.dll", "python313.dll", "python313.zip")

	if err := pinPythonPath(dir); err != nil {
		t.Fatal(err)
	}

	got, err := os.ReadFile(filepath.Join(dir, "python313._pth"))
	if err != nil {
		t.Fatalf("no ._pth beside the versioned DLL: %v", err)
	}
	lines := strings.Split(strings.TrimRight(string(got), "\r\n"), "\r\n")
	want := []string{"python313.zip", ".", "Lib", `Lib\site-packages`, "import site"}
	if strings.Join(lines, "|") != strings.Join(want, "|") {
		t.Errorf("._pth lines = %q, want %q", lines, want)
	}
	if _, err := os.Stat(filepath.Join(dir, "python3._pth")); err == nil {
		t.Error("named the ._pth after the stable-ABI shim")
	}
}

func TestARuntimeAlreadyOnDiskIsPinnedToo(t *testing.T) {
	dir := runtimeDir(t, "python3.dll", "python313.dll")
	stale := filepath.Join(dir, "python313._pth")
	if err := os.WriteFile(stale, []byte("python313.zip\r\n.\r\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	if err := pinPythonPath(dir); err != nil {
		t.Fatal(err)
	}
	first, _ := os.ReadFile(stale)
	if !strings.Contains(string(first), "import site") {
		t.Fatalf("an existing ._pth was left as it was: %q", first)
	}
	if err := pinPythonPath(dir); err != nil {
		t.Fatal(err)
	}
	second, _ := os.ReadFile(stale)
	if string(second) != string(first) {
		t.Error("a second start changed the ._pth")
	}
}

func TestARuntimeWithoutItsDLLIsReported(t *testing.T) {
	if err := pinPythonPath(runtimeDir(t, "python3.dll")); err == nil {
		t.Error("pinned a runtime that has no versioned DLL")
	}
}
