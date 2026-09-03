package service

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// A machine that already carries a root-installed cheese service is the one
// place where installing ours quietly makes things worse: both connectors run
// as the same account, dial out with the same device credential, and drive the
// same tmux server, so each one ends the other's screens. The install has to
// see it.
func TestAMachineWideConnectorIsFound(t *testing.T) {
	for _, tc := range []struct {
		manager string
		rel     string
	}{
		{"systemd", "etc/systemd/system/cheese.service"},
		{"launchd", "Library/LaunchDaemons/cheese.plist"},
	} {
		t.Run(tc.manager, func(t *testing.T) {
			root := t.TempDir()
			if got := machineWideServiceUnder(root); got != "" {
				t.Fatalf("a clean machine reported a %s connector at %q", tc.manager, got)
			}

			path := filepath.Join(root, tc.rel)
			if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(path, []byte("installed by root\n"), 0o644); err != nil {
				t.Fatal(err)
			}

			found := machineWideServiceUnder(root)
			if found != path {
				t.Fatalf("a %s connector at %s went unnoticed (got %q)", tc.manager, path, found)
			}

			// The person reading this is being asked to do the one thing we
			// will not: it has to name the file they actually have, and go
			// through the service manager rather than only deleting it.
			fix := removeMachineWideService(found)
			if !strings.Contains(fix, found) {
				t.Fatalf("the removal instruction does not name the file: %q", fix)
			}
			if tc.manager == "systemd" && !strings.Contains(fix, "systemctl") {
				t.Fatalf("a systemd unit is not removed by rm alone: %q", fix)
			}
			if tc.manager == "launchd" && !strings.Contains(fix, "launchctl") {
				t.Fatalf("a launchd job is not removed by rm alone: %q", fix)
			}
		})
	}
}
