package service

import (
	"strings"
	"testing"
	"text/template"
)

// The unit file is the only place that decides what `systemctl stop cheese`
// reaches. Everything else about not tearing sessions down is undone if systemd
// signals the whole cgroup, because the tmux server holding every `claude` on
// the machine is in it.
//
// kardianos renders this template itself, at install time, straight to
// /etc/systemd/system (or ~/.config/systemd/user) — so a template that does not
// render, or one whose fields no longer match, fails on a user's machine at
// install and nowhere earlier. These tests render it the way kardianos does and
// read the unit that comes out.

// renderUnit renders the shipped template against the field set and funcs
// kardianos passes it (service_systemd_linux.go: the anonymous `to` struct and
// the `tf` FuncMap). Anything the template names that is not here would fail on
// a real install.
//
// userName is what kardianos would put in Config.UserName. It is passed even
// though the connector never sets it, because a template that reacts to it at
// all is a template that can emit a directive systemd rejects in a user unit.
func renderUnit(t *testing.T, userName string) string {
	t.Helper()
	funcs := template.FuncMap{
		"cmd":       func(s string) string { return `"` + strings.ReplaceAll(s, `"`, `\"`) + `"` },
		"cmdEscape": func(s string) string { return strings.ReplaceAll(s, " ", `\x20`) },
	}
	tmpl, err := template.New("").Funcs(funcs).Parse(systemdScript)
	if err != nil {
		t.Fatalf("the unit template does not parse, so every install would fail: %v", err)
	}
	data := struct {
		Name                 string
		DisplayName          string
		Description          string
		UserName             string
		Arguments            []string
		Dependencies         []string
		WorkingDirectory     string
		ChRoot               string
		EnvVars              map[string]string
		Path                 string
		HasOutputFileSupport bool
		ReloadSignal         string
		PIDFile              string
		LimitNOFILE          int
		Restart              string
		SuccessExitStatus    string
		LogOutput            bool
		LogDirectory         string
	}{
		Name:        serviceName,
		Description: description,
		UserName:    userName,
		Arguments:   []string{"run", "--config", "/home/dev/.config/cheese/config.json"},
		Path:        "/home/dev/.local/bin/cheesehost",
		LimitNOFILE: -1,
		Restart:     "always",
	}
	var out strings.Builder
	if err := tmpl.Execute(&out, data); err != nil {
		t.Fatalf("the unit template does not render: %v", err)
	}
	return out.String()
}

// Measured 2026-08-17 on a Hosted machine: `systemctl kill cheese` took 20 tmux
// sessions with it, six of them mid-turn, because the default KillMode signals
// the whole control group and the tmux server lives there. Stopping the
// connector must reach the connector and stop there.
func TestStoppingTheUnitSignalsOnlyTheConnector(t *testing.T) {
	unit := renderUnit(t, "")
	if !hasDirective(unit, "KillMode", "process") {
		t.Fatalf("the unit lets systemd signal the whole cgroup, "+
			"so a stop or restart kills every hosted session:\n%s", unit)
	}
}

// The one line above is not worth a broken unit. These are the properties an
// installed cheese depends on for anything at all to run.
func TestTheUnitStillStartsAndSupervisesTheConnector(t *testing.T) {
	unit := renderUnit(t, "dev")

	execStart := directive(unit, "ExecStart")
	if !strings.Contains(execStart, "/home/dev/.local/bin/cheesehost") ||
		!strings.Contains(execStart, "run") ||
		!strings.Contains(execStart, "/home/dev/.config/cheese/config.json") {
		t.Fatalf("ExecStart does not launch the connector with its config: %q", execStart)
	}
	if !hasDirective(unit, "Restart", "always") {
		t.Fatalf("a crashed connector would never come back:\n%s", unit)
	}
	if !strings.Contains(unit, "[Unit]") || !strings.Contains(unit, "[Service]") ||
		!strings.Contains(unit, "[Install]") {
		t.Fatalf("the unit is missing a section systemd requires:\n%s", unit)
	}
}

// The connector installs into the user's own systemd, which is a different set
// of units from the system one. `multi-user.target` is not among them: measured
// on systemd 252, `systemctl --user enable` accepts a unit wanted by it and
// answers "added as a dependency to a non-existent unit", after which nothing
// ever pulls it in — so the connector runs the once that `cheese link connect`
// starts it by hand, and never again after a reboot, having reported success.
func TestTheUnitIsEnabledIntoATargetTheUserManagerHas(t *testing.T) {
	unit := renderUnit(t, "")
	if !hasDirective(unit, "WantedBy", "default.target") {
		t.Fatalf("nothing in a user systemd would ever start this unit:\n%s", unit)
	}
}

// systemd refuses User= in a user unit, and there is only one account in scope
// anyway. kardianos fills UserName in from its own Config, so the guard is that
// the template does not pass it through no matter what it is handed.
func TestTheUnitNamesNoAccount(t *testing.T) {
	for _, userName := range []string{"", "dev"} {
		unit := renderUnit(t, userName)
		if directive(unit, "User") != "" {
			t.Fatalf("a --user unit systemd will not load (UserName=%q):\n%s", userName, unit)
		}
	}
}

func directive(unit, key string) string {
	for _, line := range strings.Split(unit, "\n") {
		line = strings.TrimSpace(line)
		if after, ok := strings.CutPrefix(line, key+"="); ok {
			return after
		}
	}
	return ""
}

func hasDirective(unit, key, value string) bool {
	return directive(unit, key) == value
}
