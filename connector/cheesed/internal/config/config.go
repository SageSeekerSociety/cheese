// Package config loads and validates the cheesed JSON configuration file and
// resolves the tmux binary to use (architecture §5).
//
// cheesed is a THIN relay: it carries no policy of its own. There are no
// prompt rules, no idle/quiescence detection, no local tool API here -- all
// of that lives in the backend, which drives the agent by sending input
// frames down the same channel a human controller uses. So the config is
// small on purpose: where to dial, how to authenticate, and how to launch the
// agent in tmux.
package config

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
)

// AgentConfig describes how to launch the agent process (e.g. Claude Code)
// inside the tmux session.
type AgentConfig struct {
	Path string   `json:"path"`
	Args []string `json:"args"`
	Cwd  string   `json:"cwd"`
	Env  []string `json:"env"`
}

// Config is the root cheesed configuration document.
type Config struct {
	BackendWSURL string `json:"backend_ws_url"`
	SessionToken string `json:"session_token"`
	TmuxPath     string `json:"tmux_path"`

	Agent AgentConfig `json:"agent"`

	Cols int `json:"cols"`
	Rows int `json:"rows"`

	HeartbeatIntervalMs int `json:"heartbeat_interval_ms"`
}

// Load reads and parses the config file at path, then validates it.
func Load(path string) (*Config, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("config: read %s: %w", path, err)
	}

	var cfg Config
	if err := json.Unmarshal(raw, &cfg); err != nil {
		return nil, fmt.Errorf("config: parse %s: %w", path, err)
	}

	cfg.applyDefaults()

	if err := cfg.Validate(); err != nil {
		return nil, fmt.Errorf("config: invalid %s: %w", path, err)
	}

	return &cfg, nil
}

// applyDefaults fills in sane defaults for optional fields left unset.
func (c *Config) applyDefaults() {
	if c.Cols == 0 {
		c.Cols = 200
	}
	if c.Rows == 0 {
		c.Rows = 50
	}
	if c.HeartbeatIntervalMs == 0 {
		c.HeartbeatIntervalMs = 15000
	}
}

// Validate checks that the required fields are present and sane.
func (c *Config) Validate() error {
	if c.BackendWSURL == "" {
		return fmt.Errorf("backend_ws_url is required")
	}
	if c.SessionToken == "" {
		return fmt.Errorf("session_token is required")
	}
	if c.Agent.Path == "" {
		return fmt.Errorf("agent.path is required")
	}
	if c.Cols <= 0 || c.Rows <= 0 {
		return fmt.Errorf("cols/rows must be positive")
	}
	if c.HeartbeatIntervalMs <= 0 {
		return fmt.Errorf("heartbeat_interval_ms must be positive")
	}
	return nil
}

// ResolveTmux resolves the tmux binary to use, in order (architecture §5):
//  1. config.tmux_path, if set and it points at an existing, usable file.
//  2. a "tmux" binary sitting next to the cheesed executable.
//  3. "tmux" resolved from PATH.
func (c *Config) ResolveTmux() (string, error) {
	if c.TmuxPath != "" {
		if info, err := os.Stat(c.TmuxPath); err == nil && !info.IsDir() {
			return c.TmuxPath, nil
		}
		return "", fmt.Errorf("configured tmux_path %q is not a usable file", c.TmuxPath)
	}

	if exe, err := os.Executable(); err == nil {
		candidate := filepath.Join(filepath.Dir(exe), "tmux")
		if info, err := os.Stat(candidate); err == nil && !info.IsDir() {
			return candidate, nil
		}
	}

	found, err := exec.LookPath("tmux")
	if err != nil {
		return "", fmt.Errorf("no tmux binary found (checked tmux_path, sibling of executable, PATH): %w", err)
	}
	return found, nil
}
