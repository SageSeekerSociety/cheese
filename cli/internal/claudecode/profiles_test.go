package claudecode

import (
	"os"
	"path/filepath"
	"reflect"
	"testing"

	"github.com/SageSeekerSociety/cheese/cli/internal/place"
)

func TestEnrollmentsHaveDisjointLogins(t *testing.T) {
	ownerHome(t)
	t.Setenv(place.ProfileRootEnv, place.Root)
	_, first, err := NewLogin()
	if err != nil {
		t.Fatal(err)
	}
	fixture := []byte("first-team")
	if err := os.WriteFile(filepath.Join(first, ".credentials.json"), fixture, 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv(place.ProfileRootEnv, ".cheese/profiles/research")
	ids, err := Logins()
	if err != nil || len(ids) != 0 {
		t.Fatalf("second enrollment sees first enrollment's logins: %v, %v", ids, err)
	}
	_, second, err := NewLogin()
	if err != nil {
		t.Fatal(err)
	}
	fixture = []byte("second-team")
	if err := os.WriteFile(filepath.Join(second, ".credentials.json"), fixture, 0600); err != nil {
		t.Fatal(err)
	}
	if first == second {
		t.Fatal("two enrollments use the same credential directory")
	}
	stored, err := os.ReadFile(filepath.Join(first, ".credentials.json"))
	if err != nil || string(stored) != "first-team" {
		t.Fatalf("second enrollment changed the first one's login: %+v, %v", stored, err)
	}
}

func TestLoggingOutOneEnrollmentLeavesTheOther(t *testing.T) {
	home := ownerHome(t)
	t.Setenv(place.ProfileRootEnv, place.Root)
	_, first, _ := NewLogin()
	fixture := []byte("first-team")
	if err := os.WriteFile(filepath.Join(first, ".credentials.json"), fixture, 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv(place.ProfileRootEnv, ".cheese/profiles/research")
	_, second, _ := NewLogin()
	fixture = []byte("second-team")
	if err := os.WriteFile(filepath.Join(second, ".credentials.json"), fixture, 0600); err != nil {
		t.Fatal(err)
	}
	if err := RemoveLogin(place.FirstClaudeLogin); err != nil {
		t.Fatal(err)
	}
	stored, err := os.ReadFile(filepath.Join(first, ".credentials.json"))
	if err != nil || string(stored) != "first-team" {
		t.Fatalf("logout in second enrollment changed first login: %+v, %v", stored, err)
	}
	// Removing a later login with the same local id also belongs to this profile.
	for _, root := range []string{place.Root, ".cheese/profiles/research"} {
		t.Setenv(place.ProfileRootEnv, root)
		dir, _ := LoginDir("extra")
		if err := os.MkdirAll(dir, 0700); err != nil {
			t.Fatal(err)
		}
	}
	if err := RemoveLogin("extra"); err != nil {
		t.Fatal(err)
	}
	t.Setenv(place.ProfileRootEnv, place.Root)
	ids, err := Logins()
	if err != nil || !reflect.DeepEqual(ids, []string{"default", "extra"}) {
		t.Fatalf("logout removed another enrollment's login: %v, %v (home %s)", ids, err, filepath.Base(home))
	}
}
