// Package service runs `cheese` as a background service via kardianos/service,
// so `cheese link connect` / `cheese link auto-connect` drive it under whatever
// service manager the host uses (systemd, launchd). It is always the invoking
// account's own service manager — the connector never asks for root. The work
// itself — connecting out and hosting server-driven sessions — lives in
// internal/host; this only adapts it to the service.Interface lifecycle.
package service

import (
	"context"
	"fmt"
	"log"
	"os"
	"os/user"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	ksvc "github.com/kardianos/service"

	"github.com/SageSeekerSociety/cheese/cli/internal/host"
)

const (
	serviceName = "cheese"
	displayName = "Cheese"
	description = "Connects this machine to Cheese and hosts server-driven sessions."
)

type program struct {
	cfgPath string
	cancel  context.CancelFunc
	done    chan struct{}
}

func (p *program) Start(_ ksvc.Service) error {
	cfg, err := host.LoadConfig(p.cfgPath)
	if err != nil {
		return fmt.Errorf("service: %w", err)
	}
	h, err := host.New(cfg, p.cfgPath)
	if err != nil {
		return fmt.Errorf("service: %w", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	p.cancel = cancel
	p.done = make(chan struct{})
	go func() {
		defer close(p.done)
		if err := h.Run(ctx); err != nil && ctx.Err() == nil {
			log.Printf("cheese: host exited: %v", err)
		}
	}()
	return nil
}

func (p *program) Stop(_ ksvc.Service) error {
	if p.cancel != nil {
		p.cancel()
	}
	if p.done != nil {
		select {
		case <-p.done:
		case <-time.After(6 * time.Second):
		}
	}
	return nil
}

// New builds the kardianos service bound to the config at cfgPath. When the
// service manager launches the binary it runs `cheese run --config <cfgPath>`.
//
// It is always a PER-USER service — systemd `--user`, launchd LaunchAgent —
// installed into the invoking account's own home, because the connector has no
// business asking for the machine's root password. kardianos defaults the other
// way (a system unit, which needs root and, under systemd, a polkit agent), so
// this is the one thing we override.
//
// Nothing is lost by it on Linux: `loginctl enable-linger` (see
// KeepRunningAfterLogout) makes the user manager start at boot and outlive
// logout, which is the whole of what a system unit was buying. On macOS a
// LaunchAgent is bound to the owner's login session, and that is the honest
// answer for a machine we do not own.
func New(cfgPath string) (ksvc.Service, error) {
	cfg := &ksvc.Config{
		Name:        serviceName,
		DisplayName: displayName,
		Description: description,
		Arguments:   []string{"run", "--config", cfgPath},
		Option: ksvc.KeyValue{
			"SystemdScript": systemdScript,
			"UserService":   true,
		},
	}
	return ksvc.New(&program{cfgPath: cfgPath}, cfg)
}

// systemdScript is the unit we install. It is kardianos's own template with two
// departures, and each one decides whether an installed connector works at all.
//
// `KillMode=process`.
//
// systemd's default, KillMode=control-group, SIGTERMs every process in the
// unit's cgroup on stop. The connector's tmux server is in that cgroup (it was
// forked from this process), and so is every `claude` inside it. So `systemctl
// stop cheese`, `systemctl restart cheese`, and a reboot each took down every
// session on the machine, no matter what this program's own exit path did —
// which made "just restart the connector" a destructive act, in the one place
// nobody looks for one.
//
// With KillMode=process systemd signals the main process and nothing else. The
// connector stops; the tmux server and the sessions inside it keep running; the
// next start re-adopts them. Ending them is left to the verbs that say they end
// them (`cheese link disconnect`, `cheese uninstall`).
//
// `WantedBy=default.target`, and no `User=`.
//
// kardianos's template is written for a system unit, and both of its answers
// are wrong in a user manager. `multi-user.target` does not exist there:
// `systemctl --user enable` accepts the unit, prints "added as a dependency to
// a non-existent unit", and nothing ever starts it — so the connector would
// come up the one time `cheese link connect` starts it by hand and never again
// after a reboot, with the install having reported success. And systemd refuses
// a `User=` in a user unit outright; there is only one account in scope, the one
// whose home holds the config and the tmux socket.
//
// The template renders against kardianos's own field set and funcs (cmd,
// cmdEscape), at install time, straight into the unit directory — so a field
// name that no longer matches silently drops a line from a real machine's unit
// and nothing fails earlier. systemdunit_test.go renders it and reads the unit.
const systemdScript = `[Unit]
Description={{.Description}}
ConditionFileIsExecutable={{.Path|cmdEscape}}
{{range $i, $dep := .Dependencies}}
{{$dep}} {{end}}

[Service]
StartLimitInterval=5
StartLimitBurst=10
ExecStart={{.Path|cmdEscape}}{{range .Arguments}} {{.|cmd}}{{end}}
{{if .ChRoot}}RootDirectory={{.ChRoot|cmd}}{{end}}
{{if .WorkingDirectory}}WorkingDirectory={{.WorkingDirectory|cmdEscape}}{{end}}
{{if .ReloadSignal}}ExecReload=/bin/kill -{{.ReloadSignal}} "$MAINPID"{{end}}
{{if .PIDFile}}PIDFile={{.PIDFile|cmd}}{{end}}
{{if and .LogOutput .HasOutputFileSupport -}}
StandardOutput=file:{{.LogDirectory}}/{{.Name}}.out
StandardError=file:{{.LogDirectory}}/{{.Name}}.err
{{- end}}
{{if gt .LimitNOFILE -1 }}LimitNOFILE={{.LimitNOFILE}}{{end}}
{{if .Restart}}Restart={{.Restart}}{{end}}
{{if .SuccessExitStatus}}SuccessExitStatus={{.SuccessExitStatus}}{{end}}
KillMode=process
RestartSec=120
EnvironmentFile=-/etc/sysconfig/{{.Name}}

{{range $k, $v := .EnvVars -}}
Environment={{$k}}={{$v}}
{{end -}}

[Install]
WantedBy=default.target
`

// serviceRunsAs is the account the installed unit will run under. A per-user
// service is installed into the invoking account's own home and started by that
// account's service manager, so there is only ever one answer.
func serviceRunsAs() (uid int, name string) {
	uid = os.Getuid()
	if acct, err := user.LookupId(strconv.Itoa(uid)); err == nil {
		return uid, acct.Username
	}
	return uid, strconv.Itoa(uid)
}

// checkSelfUpdatable refuses an install that could never update itself.
//
// The connector replaces its own binary by downloading into the SAME directory
// and renaming over itself — atomically, which is the only safe way to swap a
// running executable. Both halves need write permission on the *directory*. So
// installing a service whose ExecStart points into a directory the service's own
// user cannot write produces a connector that is permanently frozen at this
// version, and — because a failed update correctly keeps the old binary running
// — frozen SILENTLY.
//
// That is not hypothetical: a device sat on a two-day-old build through a
// deploy, binary in /usr/local/bin (root-owned) with the unit running as an
// ordinary user, while every dashboard showed it healthy (#501).
//
// Refusing beats warning here. A warning at install time is read once, by
// someone who is mid-task; the consequence surfaces weeks later as "the rollout
// didn't take" with nothing pointing back to this moment.
func checkSelfUpdatable() error {
	self, err := os.Executable()
	if err != nil {
		return nil // cannot judge; never block an install on our own uncertainty
	}
	if resolved, err := filepath.EvalSymlinks(self); err == nil {
		self = resolved
	}
	uid, name := serviceRunsAs()
	return selfUpdatableIn(filepath.Dir(self), uid, name)
}

// selfUpdatableIn is the judgement, split out so it can be tested against real
// directories without pretending to be another user or reinstalling a service.
func selfUpdatableIn(dir string, uid int, name string) error {
	if uid == 0 {
		return nil // root can write anywhere; self-update will work
	}
	fi, err := os.Stat(dir)
	if err != nil {
		return nil // cannot judge
	}
	st, ok := fi.Sys().(*syscall.Stat_t)
	if !ok {
		return nil // not a POSIX filesystem; leave it alone
	}
	mode := fi.Mode().Perm()
	// Owner-writable for that uid, or world-writable. Group membership is
	// deliberately NOT consulted: resolving the target user's full group list is
	// more machinery than this is worth, and being wrong in the permissive
	// direction here restores exactly the silent failure this exists to prevent.
	if (int(st.Uid) == uid && mode&0o200 != 0) || mode&0o002 != 0 {
		return nil
	}
	return fmt.Errorf(
		"refusing to install: the service would run as %s, which cannot write to %s "+
			"— so this connector could never update itself, and the failure would be "+
			"silent (a failed update keeps the old binary running by design).\n"+
			"Install it under a directory that user owns and connect from there:\n"+
			"  curl -fsSL <origin>/connector/install.sh | sh   # → ~/.local/bin/cheesehost\n"+
			"  ~/.local/bin/cheesehost link connect",
		name, dir)
}

// machineWideService returns the path of a cheese service installed for the
// whole machine, or "" if there is none.
//
// Two connectors on one account is worse than none: they share the device
// credential the server authenticates, and they share the tmux server every
// session lives in, so each one adopts and tears down the other's screens. A
// machine-wide service belongs to root, and this command has no root and wants
// none — so it refuses and names the commands that clear it, rather than
// installing a second connector next to the first.
func machineWideService() string {
	return machineWideServiceUnder("/")
}

// machineWideServiceUnder is the search, rooted so it can be tested against a
// real directory instead of the machine's own /etc.
func machineWideServiceUnder(root string) string {
	for _, rel := range []string{
		"etc/systemd/system/" + serviceName + ".service",
		"Library/LaunchDaemons/" + serviceName + ".plist",
	} {
		path := filepath.Join(root, rel)
		if _, err := os.Stat(path); err == nil {
			return path
		}
	}
	return ""
}

// removeMachineWideService is the command that clears what machineWideService
// found — the service manager's own removal, not just the file, or the manager
// keeps the definition until the next reload.
func removeMachineWideService(path string) string {
	if strings.HasSuffix(path, ".plist") {
		return "sudo launchctl bootout system/" + serviceName + " ; sudo rm " + path
	}
	return "sudo systemctl disable --now " + serviceName + " && sudo rm " + path
}

// Control runs an install/uninstall/start/stop/restart action against the service.
func Control(cfgPath, action string) error {
	s, err := New(cfgPath)
	if err != nil {
		return err
	}
	if action != "install" {
		return ksvc.Control(s, action)
	}
	if err := checkSelfUpdatable(); err != nil {
		return err
	}
	if path := machineWideService(); path != "" {
		return fmt.Errorf(
			"refusing to install: a machine-wide cheese service is already installed "+
				"at %s, and a second connector beside it would fight this one for the "+
				"same device credential and the same tmux server.\n"+
				"Remove it (this needs root, which is why this command will not do it "+
				"for you), then run this again:\n"+
				"  %s",
			path, removeMachineWideService(path))
	}
	// An install over an existing unit REPLACES its definition, and `cheese link
	// connect` installs on every run — but kardianos refuses to overwrite ("Init
	// already exists"), so on its own a unit written by an older build would
	// outlive every reinstall and every self-update, and the machine would keep
	// the old definition forever with nothing anywhere saying so. That is #501's
	// shape exactly: a rollout that silently does not take. The service manager's
	// idea of how to stop us is not something we can afford to leave stale.
	//
	// Uninstall only AFTER the install refused, so a working unit is never
	// removed on any path that was not already about to rewrite it.
	if err := ksvc.Control(s, "install"); err == nil {
		return nil
	}
	if err := ksvc.Control(s, "uninstall"); err != nil {
		return err
	}
	return ksvc.Control(s, "install")
}

// RunForeground runs the host in the current process (used by `cheese run`).
func RunForeground(cfgPath string) error {
	s, err := New(cfgPath)
	if err != nil {
		return err
	}
	return s.Run()
}

// Status returns a human-readable service status string, asking this account's
// own service manager — the only one an install can have written to.
func Status(cfgPath string) (string, error) {
	s, err := New(cfgPath)
	if err != nil {
		return "unknown", nil
	}
	st, err := s.Status()
	if err != nil {
		return "not installed", nil
	}
	switch st {
	case ksvc.StatusRunning:
		return "running", nil
	case ksvc.StatusStopped:
		return "stopped", nil
	default:
		return "unknown", nil
	}
}
