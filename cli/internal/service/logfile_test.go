package service

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestTheConnectorLogIsAppendedAndKeptBounded(t *testing.T) {
	dir := t.TempDir()
	cfg := filepath.Join(dir, "config.json")

	for _, line := range []string{"first start\n", "second start\n"} {
		f, err := openLog(cfg)
		if err != nil {
			t.Fatal(err)
		}
		_, _ = f.WriteString(line)
		f.Close()
	}
	got, _ := os.ReadFile(filepath.Join(dir, "cheese.log"))
	if string(got) != "first start\nsecond start\n" {
		t.Fatalf("log = %q, want both starts appended", got)
	}

	if err := os.WriteFile(filepath.Join(dir, "cheese.log"), []byte(strings.Repeat("x", logLimit+1)), 0o600); err != nil {
		t.Fatal(err)
	}
	f, err := openLog(cfg)
	if err != nil {
		t.Fatal(err)
	}
	_, _ = f.WriteString("after the move\n")
	f.Close()
	got, _ = os.ReadFile(filepath.Join(dir, "cheese.log"))
	if string(got) != "after the move\n" {
		t.Errorf("an oversized log was not moved aside: %d bytes", len(got))
	}
	if info, err := os.Stat(filepath.Join(dir, "cheese.log.1")); err != nil || info.Size() != logLimit+1 {
		t.Errorf("the moved log is missing or changed: %v", err)
	}
}
