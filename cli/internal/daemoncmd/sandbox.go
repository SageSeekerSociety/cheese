package daemoncmd

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"os/user"
	"path/filepath"
	"runtime"

	"github.com/spf13/cobra"

	"github.com/SageSeekerSociety/cheese/cli/internal/place"
	"github.com/SageSeekerSociety/cheese/cli/internal/ui"
)

// apparmorDir is where the profiles AppArmor loads at boot live.
const apparmorDir = "/etc/apparmor.d"

// sandboxCmd is what only the machine's owner, as root, can do for the rooms'
// sandboxes. Ubuntu from 23.10 lets a program make a user namespace only when an
// AppArmor profile names it, and the profile Ubuntu ships names /usr/bin/bwrap,
// not the bwrap cheesehost places for its owner. `allow` writes the one that
// names it. The server asks the machine again when its owner opens the devices
// page, so nothing has to restart.
func sandboxCmd() *cobra.Command {
	allow := &cobra.Command{
		Use:   "allow",
		Short: "Let this machine's AppArmor allow the rooms' sandboxes (run with sudo)",
		Long: "Writes an AppArmor profile that lets the bubblewrap cheesehost placed for\n" +
			"you make a user namespace, which it makes each room's sandbox in, and loads\n" +
			"it. Needs root: run it as `sudo ~/.local/bin/cheesehost sandbox allow`.",
		Args: cobra.NoArgs,
		RunE: func(_ *cobra.Command, _ []string) error {
			if runtime.GOOS != "linux" {
				return errors.New("sandbox allow: only Linux restricts this with AppArmor")
			}
			if os.Geteuid() != 0 {
				return errors.New("sandbox allow: needs root; run it with sudo")
			}
			owner, err := sandboxOwner(os.Getenv("SUDO_USER"), user.Lookup, user.Current)
			if err != nil {
				return fmt.Errorf("sandbox allow: %w", err)
			}
			bwrap := filepath.Join(owner.HomeDir, place.Root, "runtime", "bubblewrap", "bwrap")
			if _, err := os.Stat(bwrap); err != nil {
				return fmt.Errorf("sandbox allow: %s is not there yet; start cheesehost so it places it, then run this again", bwrap)
			}
			path := filepath.Join(apparmorDir, "cheese-bwrap-"+owner.Username)
			if err := os.WriteFile(path, []byte(bwrapProfile("cheese-bwrap-"+owner.Username, bwrap)), 0o644); err != nil {
				return fmt.Errorf("sandbox allow: %w", err)
			}
			if out, err := exec.Command("apparmor_parser", "-r", path).CombinedOutput(); err != nil {
				return fmt.Errorf("sandbox allow: loading %s: %v: %s", path, err, out)
			}
			ui.OK("AppArmor now lets %s make the rooms' sandboxes. Open your devices page to see it.", bwrap)
			return nil
		},
	}
	group := &cobra.Command{
		Use:   "sandbox",
		Short: "The sandboxes rooms run in on this machine",
	}
	group.AddCommand(allow)
	return group
}

// sandboxOwner is whose bwrap the profile names: the person who ran sudo, or
// root itself when nobody did.
func sandboxOwner(sudoUser string, lookup func(string) (*user.User, error), current func() (*user.User, error)) (*user.User, error) {
	if sudoUser != "" && sudoUser != "root" {
		return lookup(sudoUser)
	}
	return current()
}

// bwrapProfile is the profile Ubuntu ships for /usr/bin/bwrap, naming bwrap.
func bwrapProfile(name, bwrap string) string {
	return "abi <abi/4.0>,\ninclude <tunables/global>\n\n" +
		"profile " + name + " " + bwrap + " flags=(unconfined) {\n  userns,\n}\n"
}
