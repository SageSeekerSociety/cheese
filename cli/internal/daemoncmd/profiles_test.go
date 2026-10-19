package daemoncmd

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync/atomic"
	"testing"

	"github.com/SageSeekerSociety/cheese/cli/internal/config"
	"github.com/spf13/cobra"
)

func TestEnrollingTwiceKeepsBothCredentials(t *testing.T) {
	t.Setenv("CHEESE_PROFILE_ROOT", "")
	t.Setenv("HOME", t.TempDir())
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	n := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/auth/device/start" {
			n++
			fmt.Fprintf(w, `{"device_code":"%d","approve_url":"https://example.test/approve","interval":1}`, n)
		} else {
			fmt.Fprintf(w, `{"status":"approved","token":"token-%d","device_id":"device-%d"}`, n, n)
		}
	}))
	defer server.Close()
	for enrollment := range 2 {
		root := &cobra.Command{Use: "cheese"}
		root.AddCommand(Commands()...)
		root.SetArgs([]string{"auth", "login", server.URL})
		if err := root.Execute(); err != nil {
			t.Fatal(err)
		}
		if enrollment == 0 {
			cfg, err := config.Load(config.DefaultPath())
			if err != nil {
				t.Fatal(err)
			}
			cfg.WS = "wss://control.example.test/agent"
			if err := config.Save(config.DefaultPath(), cfg); err != nil {
				t.Fatal(err)
			}
		}
	}
	first, err := config.Load(config.DefaultPath())
	if err != nil {
		t.Fatal(err)
	}
	if first.Token != "token-1" || first.DeviceID != "device-1" {
		t.Fatalf("second enrollment replaced the first: %+v", first)
	}
	paths, _ := filepath.Glob(filepath.Join(config.Dir(), "profiles", "*", "config.json"))
	if len(paths) != 1 {
		t.Fatalf("want one additional profile, got %v", paths)
	}
	second, err := config.Load(paths[0])
	if err != nil || second.Token != "token-2" || second.DeviceID != "device-2" {
		t.Fatalf("second profile is not usable: %+v, %v", second, err)
	}
	if second.WS != "wss://control.example.test/agent" {
		t.Fatalf("second profile lost the server's control URL: %q", second.WS)
	}
}

func TestConcurrentEnrollmentsCannotReplaceOneProfilesApproval(t *testing.T) {
	t.Setenv("HOME", t.TempDir())
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	t.Setenv("CHEESE_PROFILE_ROOT", "")
	started := make(chan struct{}, 1)
	var starts atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/auth/device/start" {
			starts.Add(1)
			select {
			case started <- struct{}{}:
			default:
			}
			fmt.Fprint(w, `{"device_code":"one","approve_url":"https://example.test/approve","interval":1}`)
		} else {
			fmt.Fprint(w, `{"status":"approved","token":"first-token","device_id":"first-device"}`)
		}
	}))
	defer server.Close()
	command := func() error {
		root := &cobra.Command{Use: "cheese", SilenceUsage: true, SilenceErrors: true}
		root.AddCommand(Commands()...)
		root.SetArgs([]string{"auth", "login", server.URL})
		return root.Execute()
	}
	finished := make(chan error, 1)
	go func() { finished <- command() }()
	<-started
	err := command()
	if firstErr := <-finished; firstErr != nil {
		t.Fatal(firstErr)
	}
	if err == nil {
		t.Fatalf("concurrent approval was not refused: %v", err)
	}
	if starts.Load() != 1 {
		t.Fatalf("started %d browser approvals for the same profile", starts.Load())
	}
}

func TestUninstallDefaultLeavesAnotherEnrollmentUsable(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	configDir := config.Dir()
	otherConfig := filepath.Join(configDir, "profiles", "research", "config.json")
	if err := config.Save(otherConfig, &config.Config{Token: "other-token", DeviceID: "other-device"}); err != nil {
		t.Fatal(err)
	}
	otherRoot := filepath.Join(home, ".cheese", "profiles", "research", "work", "README.md")
	if err := os.MkdirAll(filepath.Dir(otherRoot), 0700); err != nil {
		t.Fatal(err)
	}
	data, _ := json.Marshal(map[string]string{"token": "model-token"})
	if err := os.WriteFile(otherRoot, data, 0600); err != nil {
		t.Fatal(err)
	}
	cfgPath := filepath.Join(configDir, "config.json")
	if err := removeFootprint(cfgPath, home, !otherEnrollments(cfgPath)); err != nil {
		t.Fatal(err)
	}
	other, err := config.Load(otherConfig)
	if err != nil || other.Token != "other-token" {
		t.Fatalf("uninstall removed another enrollment: %+v, %v", other, err)
	}
	if _, err := os.Stat(otherRoot); err != nil {
		t.Fatalf("uninstall removed another profile's workspace: %v", err)
	}
}

func TestUninstallWithAnotherEnrollmentPreservesSharedModelLogin(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	login := filepath.Join(home, ".cheese", "claude-login", "model-service.json")
	if err := os.MkdirAll(filepath.Dir(login), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(login, []byte("shared-model"), 0600); err != nil {
		t.Fatal(err)
	}
	shared := []string{"tools/pi/bin", "skill-bundles/one/skill.md"}
	for _, relative := range shared {
		path := filepath.Join(home, ".cheese", relative)
		if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte("model-runtime"), 0600); err != nil {
			t.Fatal(err)
		}
	}
	if err := config.Save(filepath.Join(config.Dir(), "profiles", "research", "config.json"), &config.Config{Token: "other-token"}); err != nil {
		t.Fatal(err)
	}
	if err := removeFootprint(config.DefaultPath(), home, !otherEnrollments(config.DefaultPath())); err != nil {
		t.Fatal(err)
	}
	if data, err := os.ReadFile(login); err != nil || string(data) != "shared-model" {
		t.Fatalf("execution uninstall changed the shared model login: %q, %v", data, err)
	}
	for _, relative := range shared {
		if data, err := os.ReadFile(filepath.Join(home, ".cheese", relative)); err != nil || string(data) != "model-runtime" {
			t.Fatalf("execution uninstall removed shared model runtime: %q, %v", data, err)
		}
	}
}

func TestUninstallCustomConfigPreservesUnrelatedFiles(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	if err := config.Save(config.DefaultPath(), &config.Config{Token: "first"}); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "enrollment.json")
	notes := filepath.Join(filepath.Dir(path), "notes.txt")
	if err := config.Save(path, &config.Config{Token: "second"}); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(notes, []byte("mine"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := removeFootprint(path, home, !otherEnrollments(path)); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Fatalf("profile config survived: %v", err)
	}
	if data, err := os.ReadFile(notes); err != nil || string(data) != "mine" {
		t.Fatalf("uninstall touched unrelated data: %q, %v", data, err)
	}
	if first, err := config.Load(config.DefaultPath()); err != nil || first.Token != "first" {
		t.Fatalf("default enrollment changed: %+v, %v", first, err)
	}
}
