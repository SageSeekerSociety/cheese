// Package config loads and validates the cheesed JSON configuration file
// (contract §8) and resolves the tmux binary to use (contract §3).
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

// PromptRule maps a substring seen in the captured pane to a sequence of
// tmux send-keys arguments to answer it (contract §8).
type PromptRule struct {
	MatchSubstring string   `json:"match_substring"`
	Keys           []string `json:"keys"`
}

// Config is the root cheesed configuration document.
type Config struct {
	BackendWSURL   string `json:"backend_ws_url"`
	BackendHTTPURL string `json:"backend_http_url"`
	SessionToken   string `json:"session_token"`
	LocalAPIPort   int    `json:"local_api_port"`
	TmuxPath       string `json:"tmux_path"`

	Agent AgentConfig `json:"agent"`

	Cols int `json:"cols"`
	Rows int `json:"rows"`

	QuiescenceN         int `json:"quiescence_n"`
	PollIntervalMs      int `json:"poll_interval_ms"`
	HeartbeatIntervalMs int `json:"heartbeat_interval_ms"`

	PromptRules []PromptRule `json:"prompt_rules"`
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
	if c.LocalAPIPort == 0 {
		c.LocalAPIPort = 47615
	}
	if c.Cols == 0 {
		c.Cols = 200
	}
	if c.Rows == 0 {
		c.Rows = 50
	}
	if c.QuiescenceN == 0 {
		c.QuiescenceN = 3
	}
	if c.PollIntervalMs == 0 {
		c.PollIntervalMs = 1000
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
	if c.BackendHTTPURL == "" {
		return fmt.Errorf("backend_http_url is required")
	}
	if c.SessionToken == "" {
		return fmt.Errorf("session_token is required")
	}
	if c.Agent.Path == "" {
		return fmt.Errorf("agent.path is required")
	}
	if c.LocalAPIPort <= 0 || c.LocalAPIPort > 65535 {
		return fmt.Errorf("local_api_port out of range: %d", c.LocalAPIPort)
	}
	if c.Cols <= 0 || c.Rows <= 0 {
		return fmt.Errorf("cols/rows must be positive")
	}
	if c.QuiescenceN <= 0 {
		return fmt.Errorf("quiescence_n must be positive")
	}
	if c.PollIntervalMs <= 0 {
		return fmt.Errorf("poll_interval_ms must be positive")
	}
	if c.HeartbeatIntervalMs <= 0 {
		return fmt.Errorf("heartbeat_interval_ms must be positive")
	}
	return nil
}

// ResolveTmux resolves the tmux binary to use, in the order mandated by
// contract §3:
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
