// Package service runs `cheese` as a cross-platform system service via
// kardianos/service, so `cheese link connect` / `cheese link auto-connect` drive
// it under whatever init system the host uses (systemd, openrc, launchd). The
// work itself — connecting out and hosting server-driven sessions — lives in
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
// kardianos adapts to the init system but not to privilege: by default it
// installs a system-level unit, which needs root (and, under systemd, a polkit
// agent to prompt for it). We adapt that ourselves — running as a non-root user
// installs a per-user service instead (systemd `--user`, launchd LaunchAgent),
// which needs no root and no polkit. A machine's config already lives in the
// user's home, so per-user is the natural default for an unprivileged install.
func New(cfgPath string) (ksvc.Service, error) {
	// Default to a per-user service when unprivileged, a system service when root.
	return newService(cfgPath, os.Geteuid() != 0)
}

// newService builds the kardianos service as either a per-user or a system unit.
func newService(cfgPath string, userService bool) (ksvc.Service, error) {
	cfg := &ksvc.Config{
		Name:        serviceName,
		DisplayName: displayName,
		Description: description,
		Arguments:   []string{"run", "--config", cfgPath},
		Option:      ksvc.KeyValue{"SystemdScript": systemdScript},
	}
	if userService {
		// A per-user service (systemd --user / launchd LaunchAgent): no root or polkit.
		cfg.Option["UserService"] = true
	} else if u := os.Getenv("SUDO_USER"); u != "" && u != "root" {
		// A *system* unit that starts at boot with nobody logged in — but run it AS the
		// invoking user so it uses that user's home (config + private tmux) and gets a
		// real $HOME (restish and tmux both need one; systemd/launchd populate HOME from
		// the account database when User is set).
		cfg.UserName = u
	}
	return ksvc.New(&program{cfgPath: cfgPath}, cfg)
}

// systemdScript is the unit we install. It is kardianos's own template with one
// line added — `KillMode=process` — and that line is the whole reason we carry a
// template at all.
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
{{if .UserName}}User={{.UserName}}{{end}}
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
WantedBy=multi-user.target
`

// serviceRunsAs is the account the installed unit will run under: the invoking
// user normally, and SUDO_USER for a system unit installed with sudo (mirroring
// newService, which sets cfg.UserName the same way).
func serviceRunsAs() (uid int, name string) {
	if os.Geteuid() == 0 {
		if u := os.Getenv("SUDO_USER"); u != "" && u != "root" {
			if acct, err := user.Lookup(u); err == nil {
				if id, err := strconv.Atoi(acct.Uid); err == nil {
					return id, u
				}
			}
		}
		return 0, "root"
	}
	return os.Getuid(), strconv.Itoa(os.Getuid())
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
			"Install it under a directory that user owns and register the service from "+
			"there:\n"+
			"  curl -fsSL <origin>/connector/install.sh | sh   # → ~/.local/bin/cheesehost\n"+
			"  ~/.local/bin/cheesehost service install",
		name, dir)
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
	// An install over an existing unit REPLACES its definition. kardianos refuses
	// to overwrite ("Init already exists"), and `cheese link connect` installs on
	// every run and ignores the error — so a unit written by an older build would
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

// Status returns a human-readable service status string. It looks for the service under
// both privilege variants — the one matching our own euid first, then the other — so a
// normal-user `cheese status` still reports a *system* service installed via sudo (and
// vice versa). Querying a system unit's state needs no root. "not installed" means
// neither variant exists.
func Status(cfgPath string) (string, error) {
	prefUser := os.Geteuid() != 0
	var lastErr error
	for _, userService := range []bool{prefUser, !prefUser} {
		s, err := newService(cfgPath, userService)
		if err != nil {
			lastErr = err
			continue
		}
		st, err := s.Status()
		if err != nil {
			lastErr = err // most likely ErrNotInstalled for this variant — try the other
			continue
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
	if lastErr != nil {
		return "not installed", nil
	}
	return "unknown", nil
}
