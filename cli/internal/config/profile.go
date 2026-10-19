package config

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"

	"github.com/SageSeekerSociety/cheese/cli/internal/claudecode"
	"github.com/SageSeekerSociety/cheese/cli/internal/place"
)

var profileName = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,39}$`)

var CLIProfile = "default"
var CLIProfileSet bool

func CommandPath() string {
	path, _ := ProfilePath(CLIProfile)
	return path
}

// ProfilePath keeps the original installation as the default profile.
func ProfilePath(name string) (string, error) {
	if name == "" || name == "default" {
		return DefaultPath(), nil
	}
	if !profileName.MatchString(name) {
		return "", fmt.Errorf("invalid profile %q: use 1-40 lowercase letters, digits or hyphens", name)
	}
	return filepath.Join(Dir(), "profiles", name, "config.json"), nil
}

// Profile identifies a config independently of the process that opens it.
func Profile(path string) string {
	absolute, _ := filepath.Abs(path)
	standard, _ := filepath.Abs(DefaultPath())
	if absolute == standard {
		return "default"
	}
	parent := filepath.Dir(absolute)
	if filepath.Dir(parent) == filepath.Join(Dir(), "profiles") && filepath.Base(absolute) == "config.json" {
		return filepath.Base(parent)
	}
	// Existing --config installations are also the first (default) profile.
	return "default"
}

func ServiceName(path string) string {
	if name := Profile(path); name != "default" {
		return "cheese-" + name
	}
	return "cheese"
}

// Activate supplies the enrollment root to helpers and remote commands. Each
// service is a separate process; changing profiles never changes the OS HOME.
func Activate(path string) error {
	// Old connectors shared logins across enrollments. A single enrollment
	// inherits them; with several, the original default enrollment owns them.
	paths, err := filepath.Glob(filepath.Join(Dir(), "profiles", "*", "config.json"))
	if err != nil {
		return err
	}
	paths = append(paths, DefaultPath())
	var enrolled []string
	for _, candidate := range paths {
		if cfg, err := Load(candidate); err == nil && cfg.Token != "" {
			enrolled = append(enrolled, candidate)
		}
	}
	ownerRoot := place.Root
	if len(enrolled) == 1 {
		ownerRoot = Root(enrolled[0])
	}
	if err := claudecode.MigrateLogins(ownerRoot); err != nil {
		return err
	}
	return os.Setenv(place.ProfileRootEnv, Root(path))
}

func Root(path string) string {
	root := place.Root
	if name := Profile(path); name != "default" {
		root = filepath.Join(root, "profiles", name)
	}
	return filepath.ToSlash(root)
}

// ReserveEnrollment reserves a fresh profile before the browser flow, so two
// simultaneous enrollments cannot choose the same identity.
func ReserveEnrollment() (string, error) {
	for n := 2; ; n++ {
		candidate := filepath.Join(Dir(), "profiles", fmt.Sprintf("profile-%d", n))
		if err := os.MkdirAll(filepath.Dir(candidate), 0700); err != nil {
			return "", err
		}
		if err := os.Mkdir(candidate, 0700); os.IsExist(err) {
			continue
		} else if err != nil {
			return "", err
		}
		return filepath.Join(candidate, "config.json"), nil
	}
}
