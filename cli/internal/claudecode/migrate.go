package claudecode

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"

	"github.com/SageSeekerSociety/cheese/cli/internal/place"
)

// MigrateLogins moves the old shared store once. Nothing reads it after this;
// failure leaves the source available for the next start, never overwritten.
func MigrateLogins(ownerRoot string) error {
	home, err := os.UserHomeDir()
	if err != nil {
		return err
	}
	shared := filepath.Join(home, place.Root)
	first := filepath.Join(shared, "claude-login")
	more := filepath.Join(shared, "claude-logins")
	_, firstErr := os.Stat(first)
	_, moreErr := os.Stat(more)
	if errors.Is(firstErr, os.ErrNotExist) && errors.Is(moreErr, os.ErrNotExist) {
		return nil
	}
	unlock, err := lockMigration(filepath.Join(shared, ".claude-logins-migration.lock"))
	if err != nil {
		return fmt.Errorf("claude: lock login migration: %w", err)
	}
	defer unlock()
	root := filepath.Join(home, ownerRoot)
	if err := moveLogin(first, filepath.Join(root, place.ClaudeLogin)); err != nil {
		return err
	}
	entries, err := os.ReadDir(more)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	for _, entry := range entries {
		if err := moveLogin(filepath.Join(more, entry.Name()), filepath.Join(root, place.ClaudeLogins, entry.Name())); err != nil {
			return err
		}
	}
	return os.Remove(more)
}

func moveLogin(source, destination string) error {
	if _, err := os.Stat(source); errors.Is(err, os.ErrNotExist) {
		return nil
	} else if err != nil {
		return err
	}
	if _, err := os.Stat(destination); err == nil {
		return fmt.Errorf("claude: cannot migrate %s: destination %s already exists; both retained", source, destination)
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(destination), 0700); err != nil {
		return err
	}
	finish, err := relocateKeychain(source, destination)
	if err != nil {
		return err
	}
	if err := os.Rename(source, destination); err != nil {
		return fmt.Errorf("claude: move login: %w", err)
	}
	if err := finish(); err != nil {
		// The old Keychain entry still exists when removal fails. Restore its
		// directory so a subsequent start can retry the same migration.
		return errors.Join(err, os.Rename(destination, source))
	}
	return nil
}
