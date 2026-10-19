package claudecode

import (
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

func TestMovingALoginPreservesItsKeychainCredential(t *testing.T) {
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
	if err := exec.Command("security", "add-generic-password", "-a", "test", "-s", keychainService(source), "-w", credential).Run(); err != nil {
		t.Fatal(err)
	}
	if err := moveLogin(source, destination); err != nil {
		t.Fatal(err)
	}
	got, err := readKeychain(keychainService(destination))
	if err != nil || string(got) != credential {
		t.Fatalf("moved login lost its credential: %v", err)
	}
	old, err := readKeychain(keychainService(source))
	if err != nil || old != nil {
		t.Fatalf("old shared credential remains: %v", err)
	}
	if _, err := os.Stat(source); !os.IsNotExist(err) {
		t.Fatalf("old shared login directory remains: %v", err)
	}
}
