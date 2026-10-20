package daemoncmd

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/SageSeekerSociety/cheese/cli/internal/claudecode"
	"github.com/SageSeekerSociety/cheese/cli/internal/config"
	"github.com/SageSeekerSociety/cheese/cli/internal/place"
)

func TestUpgradeMovesSharedLoginsIntoTheSingleEnrollment(t *testing.T) {
	for _, profile := range []string{"default", "research"} {
		t.Run(profile, func(t *testing.T) {
			home := t.TempDir()
			t.Setenv("HOME", home)
			t.Setenv("USERPROFILE", home)
			t.Setenv("XDG_CONFIG_HOME", t.TempDir())
			t.Setenv(place.ProfileRootEnv, "")
			path, _ := config.ProfilePath(profile)
			if err := config.Save(path, &config.Config{Token: "enrollment"}); err != nil {
				t.Fatal(err)
			}
			for _, relative := range []string{"claude-login", "claude-logins/extra"} {
				old := filepath.Join(home, place.Root, relative)
				if err := os.MkdirAll(old, 0700); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(filepath.Join(old, ".credentials.json"), []byte(relative), 0600); err != nil {
					t.Fatal(err)
				}
			}
			if err := config.Activate(path); err != nil {
				t.Fatal(err)
			}
			for id, relative := range map[string]string{"default": "claude-login", "extra": "claude-logins/extra"} {
				dir := filepath.Join(home, config.Root(path), "logins", relative)
				credential, err := os.ReadFile(filepath.Join(dir, ".credentials.json"))
				if err != nil || string(credential) != relative {
					t.Fatalf("upgrade lost login %s: %+v, %v", id, credential, err)
				}
				if _, err := os.Stat(filepath.Join(home, place.Root, relative)); !os.IsNotExist(err) {
					t.Fatalf("shared login %s still exists: %v", relative, err)
				}
				resolved, err := claudecode.LoginDir(id)
				if err != nil || resolved != dir {
					t.Fatalf("login %s resolves to %s, %v; want %s", id, resolved, err, dir)
				}
			}
			if err := config.Activate(path); err != nil {
				t.Fatalf("second start repeats migration: %v", err)
			}
		})
	}
}

// A Claude session started before the upgrade keeps its old CLAUDE_CONFIG_DIR
// and writes a fresh .claude.json there after the login moved. Seen on a
// machine whose connector then exited on every start for a day.
func TestStartAfterASurvivingSessionRecreatedTheOldLogin(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	t.Setenv(place.ProfileRootEnv, "")
	path := config.DefaultPath()
	if err := config.Save(path, &config.Config{Token: "enrollment"}); err != nil {
		t.Fatal(err)
	}
	write := func(relative, content string) {
		file := filepath.Join(home, place.Root, relative)
		if err := os.MkdirAll(filepath.Dir(file), 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(file, []byte(content), 0600); err != nil {
			t.Fatal(err)
		}
	}
	// The state the earlier start left: the login moved, then recreated.
	write(".claude-logins-migration.lock", "")
	write("logins/claude-login/.claude.json", `{"oauthAccount":{"emailAddress":"owner@example.com"}}`)
	write("logins/claude-login/backups/.claude.json.backup", "older")
	write("claude-login/.claude.json", `{"firstStartTime":"2026-10-19T17:36:38Z","migrationVersion":12}`)
	write("claude-login/backups/.claude.json.backup", "fresh")
	if err := os.MkdirAll(filepath.Join(home, place.Root, "claude-login", ".claude.json.lock"), 0755); err != nil {
		t.Fatal(err)
	}

	for start := 1; start <= 2; start++ {
		if err := config.Activate(path); err != nil {
			t.Fatalf("start %d: connector cannot start: %v", start, err)
		}
	}
	for relative, want := range map[string]string{
		"logins/claude-login/.claude.json":                `{"oauthAccount":{"emailAddress":"owner@example.com"}}`,
		"logins/claude-login/backups/.claude.json.backup": "older",
		"claude-login/.claude.json":                       `{"firstStartTime":"2026-10-19T17:36:38Z","migrationVersion":12}`,
		"claude-login/backups/.claude.json.backup":        "fresh",
	} {
		got, err := os.ReadFile(filepath.Join(home, place.Root, relative))
		if err != nil || string(got) != want {
			t.Fatalf("%s changed: %q, %v; want %q", relative, got, err, want)
		}
	}
	dir, err := claudecode.LoginDir(place.FirstClaudeLogin)
	if want := filepath.Join(home, place.Root, "logins", "claude-login"); err != nil || dir != want {
		t.Fatalf("first login resolves to %s, %v; want %s", dir, err, want)
	}
}

// A login logged out after the upgrade stays logged out, even when a session
// started before the upgrade recreated its old directory.
func TestLoggedOutLoginIsNotRestoredFromARecreatedOldDirectory(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	t.Setenv(place.ProfileRootEnv, "")
	path := config.DefaultPath()
	if err := config.Save(path, &config.Config{Token: "enrollment"}); err != nil {
		t.Fatal(err)
	}
	old := filepath.Join(home, place.Root, "claude-logins", "extra")
	recreate := func() {
		if err := os.MkdirAll(old, 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(old, ".claude.json"), []byte("{}"), 0600); err != nil {
			t.Fatal(err)
		}
	}
	recreate()
	if err := config.Activate(path); err != nil {
		t.Fatal(err)
	}
	recreate()
	if err := claudecode.RemoveLogin("extra"); err != nil {
		t.Fatal(err)
	}
	if err := config.Activate(path); err != nil {
		t.Fatalf("connector cannot start: %v", err)
	}
	logins, err := claudecode.Logins()
	if err != nil {
		t.Fatal(err)
	}
	for _, id := range logins {
		if id == "extra" {
			t.Fatalf("logged-out login came back: %v", logins)
		}
	}
	if _, err := os.Stat(filepath.Join(old, ".claude.json")); err != nil {
		t.Fatalf("recreated old directory was not left in place: %v", err)
	}
}
