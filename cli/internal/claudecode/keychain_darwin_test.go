package claudecode

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"testing"

	"github.com/SageSeekerSociety/cheese/cli/internal/place"
)

func TestMovingAFileBackedLoginKeepsItsCredential(t *testing.T) {
	home := ownerHome(t)
	source := filepath.Join(home, ".cheese/claude-login")
	destination := filepath.Join(home, ".cheese/logins/claude-login")
	if err := os.MkdirAll(source, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(source, ".credentials.json"), []byte("synthetic-file-credential"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := MigrateLogins(place.Root); err != nil {
		t.Fatal(err)
	}
	reader := filepath.Join(home, "read-file-login")
	if err := os.WriteFile(reader, []byte("#!/bin/sh\nexec cat \"${CLAUDE_SECURESTORAGE_CONFIG_DIR:-$CLAUDE_CONFIG_DIR}/.credentials.json\"\n"), 0700); err != nil {
		t.Fatal(err)
	}
	got, err := Command(context.Background(), reader, destination).Output()
	if err != nil || string(got) != "synthetic-file-credential" {
		t.Fatalf("moved file-backed login is unreadable: got %q, %v", got, err)
	}
}

func TestMovingALoginKeepsAnActiveSessionsCredentialUpdates(t *testing.T) {
	home := ownerHome(t)
	keychain := filepath.Join(home, "test.keychain-db")
	if out, err := exec.Command("/usr/bin/security", "create-keychain", "-p", "test", keychain).CombinedOutput(); err != nil {
		t.Fatalf("create isolated test keychain: %s, %v", out, err)
	}
	t.Cleanup(func() { exec.Command("/usr/bin/security", "delete-keychain", keychain).Run() })
	if err := exec.Command("/usr/bin/security", "unlock-keychain", "-p", "test", keychain).Run(); err != nil {
		t.Fatal(err)
	}
	// Every operation targets this temporary keychain explicitly. The user's
	// login keychain and search list are never used or changed.
	shim := filepath.Join(home, "bin")
	if err := os.MkdirAll(shim, 0700); err != nil {
		t.Fatal(err)
	}
	script := `#!/bin/sh
if [ "$1" = -i ]; then
  while IFS= read -r line; do printf '%s "%s"\n' "$line" "$TEST_KEYCHAIN"; done | /usr/bin/security -i
else
  exec /usr/bin/security "$@" "$TEST_KEYCHAIN"
fi
`
	if err := os.WriteFile(filepath.Join(shim, "security"), []byte(script), 0700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", shim+":"+os.Getenv("PATH"))
	t.Setenv("TEST_KEYCHAIN", keychain)
	source := filepath.Join(home, ".cheese/claude-login")
	destination := filepath.Join(home, ".cheese/logins/claude-login")
	if err := os.MkdirAll(source, 0700); err != nil {
		t.Fatal(err)
	}
	const credential = `{"claudeAiOauth":{"accessToken":"synthetic-token","refreshToken":"synthetic-refresh"}}`
	// The fixture follows the pinned Claude build's Keychain protocol and
	// independently seeds both the Console and OAuth items.
	hash := sha256.Sum256([]byte(source))
	for _, kind := range []string{"", "-credentials"} {
		service := fmt.Sprintf("Claude Code%s-%x", kind, hash[:4])
		if err := exec.Command("security", "add-generic-password", "-a", "test", "-s", service, "-w", credential).Run(); err != nil {
			t.Fatal(err)
		}
	}
	if err := MigrateLogins(place.Root); err != nil {
		t.Fatal(err)
	}
	reader := filepath.Join(home, "read-login")
	if err := os.WriteFile(reader, []byte(`#!/bin/sh
store="${CLAUDE_SECURESTORAGE_CONFIG_DIR:-$CLAUDE_CONFIG_DIR}"
service="Claude Code${2}-$(printf %s "$store" | shasum -a 256 | cut -c1-8)"
case "$1" in
  read) exec security find-generic-password -a test -s "$service" -w ;;
  argv) exec security add-generic-password -U -a test -s "$service" -X "$3" ;;
  stdin) printf 'add-generic-password -U -a "test" -s "%s" -X "%s"\n' "$service" "$3" | security -i ;;
  delete) exec security delete-generic-password -a test -s "$service" ;;
esac
`), 0700); err != nil {
		t.Fatal(err)
	}
	call := func(args ...string) []byte {
		t.Helper()
		got, err := Command(context.Background(), reader, destination, args...).CombinedOutput()
		if err != nil {
			t.Fatalf("migrated login command %s: %v: %s", args[0], err, got)
		}
		return got
	}
	for _, kind := range []string{"", "-credentials"} {
		if got := string(call("read", kind)); got != credential+"\n" {
			t.Fatalf("migrated login lost its credential: %q", got)
		}
		service := fmt.Sprintf("Claude Code%s-%x", kind, hash[:4])
		// A process started before migration still updates the original item.
		const refreshed = "synthetic-retained-session-update"
		if err := exec.Command("security", "add-generic-password", "-U", "-a", "test", "-s", service, "-w", refreshed).Run(); err != nil {
			t.Fatal(err)
		}
		if got := string(call("read", kind)); got != refreshed+"\n" {
			t.Fatalf("migrated login lost retained session's update: %q", got)
		}
		// Pinned Claude uses interactive input normally and argv for large
		// credentials. Both must update the same item the old session reads.
		for _, mode := range []string{"argv", "stdin"} {
			updated := "synthetic-migrated-session-" + mode
			call(mode, kind, hex.EncodeToString([]byte(updated)))
			got, err := exec.Command("security", "find-generic-password", "-a", "test", "-s", service, "-w").Output()
			if err != nil || string(got) != updated+"\n" {
				t.Fatalf("retained session cannot read migrated session's %s update: %q, %v", mode, got, err)
			}
		}
		call("delete", kind)
		if err := exec.Command("security", "find-generic-password", "-a", "test", "-s", service, "-w").Run(); err == nil {
			t.Fatal("logout left the migrated credential available")
		}
	}
	if _, err := os.Stat(source); !os.IsNotExist(err) {
		t.Fatalf("old shared login directory remains: %v", err)
	}
}
