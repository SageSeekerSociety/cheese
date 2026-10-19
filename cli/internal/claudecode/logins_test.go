package claudecode

import (
	"os"
	"path/filepath"
	"reflect"
	"testing"

	"github.com/SageSeekerSociety/cheese/cli/internal/place"
)

func ownerHome(t *testing.T) string {
	t.Helper()
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	return home
}

// A machine that has logged in once keeps that login where it always was: on
// macOS its Keychain item is named after the path, so moving it would log the
// owner out.
func TestAMachinesFirstLoginStaysInItsDirectory(t *testing.T) {
	home := ownerHome(t)
	first := filepath.Join(home, place.Root, place.ClaudeLogin)
	if err := os.MkdirAll(first, 0o700); err != nil {
		t.Fatal(err)
	}

	dir, err := LoginDir(place.FirstClaudeLogin)
	if err != nil || dir != first {
		t.Fatalf("LoginDir(first) = %q, %v; want %q", dir, err, first)
	}
	ids, err := Logins()
	if err != nil || !reflect.DeepEqual(ids, []string{place.FirstClaudeLogin}) {
		t.Fatalf("Logins() = %v, %v; want only the first login", ids, err)
	}
}

// Each `claude login` adds a login of its own: the first one while the machine
// has none, then one directory per login beside it, the earlier ones untouched.
func TestEachLoginAddedIsItsOwn(t *testing.T) {
	ownerHome(t)

	firstID, firstDir, err := NewLogin()
	if err != nil || firstID != place.FirstClaudeLogin {
		t.Fatalf("first NewLogin() = %q, %v; want the first login", firstID, err)
	}
	if err := os.WriteFile(filepath.Join(firstDir, ".credentials.json"), []byte("a"), 0o600); err != nil {
		t.Fatal(err)
	}
	secondID, secondDir, err := NewLogin()
	if err != nil {
		t.Fatal(err)
	}
	thirdID, _, err := NewLogin()
	if err != nil {
		t.Fatal(err)
	}

	if secondID == firstID || thirdID == secondID {
		t.Fatalf("ids %q, %q, %q: each login needs its own", firstID, secondID, thirdID)
	}
	if secondDir == firstDir {
		t.Fatal("a second login shares the first one's directory")
	}
	if data, err := os.ReadFile(filepath.Join(firstDir, ".credentials.json")); err != nil || string(data) != "a" {
		t.Fatalf("the first login's credentials changed: %q, %v", data, err)
	}
	ids, err := Logins()
	if err != nil || len(ids) != 3 || ids[0] != place.FirstClaudeLogin {
		t.Fatalf("Logins() = %v, %v; want the three, the first one first", ids, err)
	}
	if dir, err := LoginDir(secondID); err != nil || dir != secondDir {
		t.Fatalf("LoginDir(%q) = %q, %v; want %q", secondID, dir, err, secondDir)
	}
}

// Logging one login out leaves the others; a further login is forgotten with
// its directory, the first keeps its directory as it always did.
func TestLoggingOneLoginOutLeavesTheOthers(t *testing.T) {
	ownerHome(t)
	first, _, _ := NewLogin()
	second, _, _ := NewLogin()
	third, _, _ := NewLogin()

	if err := RemoveLogin(second); err != nil {
		t.Fatal(err)
	}
	if err := RemoveLogin(first); err != nil {
		t.Fatal(err)
	}

	ids, err := Logins()
	if err != nil || !reflect.DeepEqual(ids, []string{first, third}) {
		t.Fatalf("Logins() = %v, %v; want %v", ids, err, []string{first, third})
	}
}

// A login's id becomes part of a path, here and in a session's launch.
func TestAnIDThatIsNotALoginNameIsRefused(t *testing.T) {
	ownerHome(t)
	for _, id := range []string{"", "..", "../claude-login", "a/b", "has space"} {
		if dir, err := LoginDir(id); err == nil {
			t.Errorf("LoginDir(%q) = %q; want it refused", id, dir)
		}
	}
}
