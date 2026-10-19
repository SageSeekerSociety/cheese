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
				if err := claudecode.SaveModelService(old, claudecode.ModelService{
					BaseURL: "https://example.test", Token: relative, Model: "model",
				}); err != nil {
					t.Fatal(err)
				}
			}
			if err := config.Activate(path); err != nil {
				t.Fatal(err)
			}
			for id, relative := range map[string]string{"default": "claude-login", "extra": "claude-logins/extra"} {
				dir := filepath.Join(home, config.Root(path), "logins", relative)
				service, err := claudecode.LoadModelService(dir)
				if err != nil || service == nil || service.Token != relative {
					t.Fatalf("upgrade lost login %s: %+v, %v", id, service, err)
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
