package claudecode

import (
	"context"
	"crypto/sha256"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"testing"

	"github.com/SageSeekerSociety/cheese/cli/internal/place"
)

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
	// The fixture follows the pinned Claude build's Keychain protocol. The
	// connector neither computes these service names nor reads the credential.
	hash := sha256.Sum256([]byte(source))
	service := fmt.Sprintf("Claude Code-credentials-%x", hash[:4])
	if err := exec.Command("security", "add-generic-password", "-a", "test", "-s", service, "-w", credential).Run(); err != nil {
		t.Fatal(err)
	}
	if err := MigrateLogins(place.Root); err != nil {
		t.Fatal(err)
	}
	reader := filepath.Join(home, "read-login")
	if err := os.WriteFile(reader, []byte(`#!/bin/sh
store="${CLAUDE_SECURESTORAGE_CONFIG_DIR:-$CLAUDE_CONFIG_DIR}"
service="Claude Code-credentials-$(printf %s "$store" | shasum -a 256 | cut -c1-8)"
exec security find-generic-password -s "$service" -w
`), 0700); err != nil {
		t.Fatal(err)
	}
	read := func(expected string) {
		t.Helper()
		got, err := Command(context.Background(), reader, destination).Output()
		if err != nil || string(got) != expected+"\n" {
			t.Fatalf("enrollment cannot read its latest credential: %v", err)
		}
	}
	read(credential)
	// A process started before the upgrade still writes the namespace it was
	// launched with. Its successful refresh must be visible to the enrollment.
	const refreshed = `{"claudeAiOauth":{"accessToken":"synthetic-updated","refreshToken":"synthetic-refresh-updated"}}`
	if err := exec.Command("security", "add-generic-password", "-U", "-a", "test", "-s", service, "-w", refreshed).Run(); err != nil {
		t.Fatal(err)
	}
	read(refreshed)
	if _, err := os.Stat(source); !os.IsNotExist(err) {
		t.Fatalf("old shared login directory remains: %v", err)
	}
}
