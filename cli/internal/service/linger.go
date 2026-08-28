package service

import (
	"fmt"
	"os/exec"
	"runtime"
	"strings"
)

// KeepRunningAfterLogout makes the per-user service outlive the login session
// that installed it. Call it once an install has succeeded.
//
// A user service manager is started with a user's first login session and torn
// down with their last, so on its own a per-user connector stops when the ssh
// session that installed it closes, and a machine that reboots with nobody
// logging in never comes back. Being immune to that is the entire thing a
// system-wide unit was buying, and systemd gives it away without root:
// `loginctl enable-linger` starts the user manager at boot and keeps it up with
// no session open. Turning it on for YOURSELF is not an administrator action —
// systemd's shipped policy has org.freedesktop.login1.set-self-linger at
// allow_any=yes, unlike set-user-linger, which is for doing it to somebody else.
//
// macOS is a different machine and gets no call to make: a LaunchAgent lives in
// the owner's login session (launchd puts it in the gui/<uid> domain, created by
// loginwindow), and there is no unprivileged way to place one anywhere else. It
// starts at their login and stops at their logout, which on a personal Mac is a
// non-event and on a Mac nobody logs into is a machine that never hosts. That is
// the honest cost of not asking for someone's admin password, and it is written
// down in docs/device-self-hosting.md rather than warned about on every connect.
func KeepRunningAfterLogout() error {
	if runtime.GOOS != "linux" {
		return nil
	}
	_, name := serviceRunsAs()
	enableOut, enableErr := exec.Command("loginctl", "enable-linger").CombinedOutput()
	// Confirm rather than trust the exit code: this is the difference between a
	// machine that comes back after a reboot and one that quietly does not, and
	// asking costs one more call. Read the `Linger=yes` line rather than pass
	// --value, so an older loginctl answers the question instead of erroring out
	// and making a working machine look broken.
	shown, _ := exec.Command("loginctl", "show-user", name, "-p", "Linger").Output()
	if strings.TrimSpace(string(shown)) == "Linger=yes" {
		return nil
	}
	detail := strings.TrimSpace(string(enableOut))
	if detail == "" && enableErr != nil {
		detail = enableErr.Error()
	}
	if detail == "" {
		detail = "loginctl reported no error, but linger is still off"
	}
	return fmt.Errorf(
		"linger could not be turned on for %s: %s\n"+
			"    This machine will host sessions now, but it drops off the moment %s "+
			"logs out and does not come back after a reboot.\n"+
			"    One command from an administrator fixes it for good:\n"+
			"        sudo loginctl enable-linger %s",
		name, detail, name, name)
}
