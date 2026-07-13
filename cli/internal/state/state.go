// Package state shares one small runtime fact between the long-running host and
// the ad-hoc CLI commands: how many screens are currently hosted. The host
// rewrites the file as screens come and go; commands like `cheese link
// disconnect` read it to warn before killing live work. Best-effort by design —
// a missing or stale file just reads as zero.
package state

import (
	"encoding/json"
	"os"
	"path/filepath"
	"syscall"
)

type snapshot struct {
	Screens int `json:"screens"`
	PID     int `json:"pid"`
}

// Path derives the state file location from the config path (same directory).
func Path(cfgPath string) string {
	return filepath.Join(filepath.Dir(cfgPath), "state.json")
}

// Write records the current number of hosted screens.
func Write(cfgPath string, screens int) {
	data, err := json.Marshal(snapshot{Screens: screens, PID: os.Getpid()})
	if err != nil {
		return
	}
	_ = os.WriteFile(Path(cfgPath), data, 0o600)
}

// Clear removes the state file (host shutdown).
func Clear(cfgPath string) { _ = os.Remove(Path(cfgPath)) }

// Screens reports the recorded screen count, verifying the writer is still
// alive so a crashed host doesn't leave a scary stale warning behind.
func Screens(cfgPath string) int {
	data, err := os.ReadFile(Path(cfgPath))
	if err != nil {
		return 0
	}
	var s snapshot
	if json.Unmarshal(data, &s) != nil {
		return 0
	}
	if s.PID > 0 {
		if p, err := os.FindProcess(s.PID); err != nil || p.Signal(syscall.Signal(0)) != nil {
			return 0
		}
	}
	return s.Screens
}
