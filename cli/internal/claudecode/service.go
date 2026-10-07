package claudecode

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strings"

	"github.com/SageSeekerSociety/cheese/cli/internal/place"
)

// ModelService is a model service other than Anthropic's that the owner points
// the platform's Claude Code at: one that speaks Anthropic's API, such as GLM,
// Kimi, DeepSeek or a relay of the owner's own. It lives beside the login, on
// this machine only; the platform never sees the key. While it is set, every
// session of the owner's Claude Code here calls it instead of the login.
type ModelService struct {
	BaseURL string `json:"base_url"`
	Token   string `json:"token"`
	Model   string `json:"model"`
}

// The file the launch reads and the platform's login probe looks for.
const serviceFile = place.ModelService

// Check says what is wrong with the service as given, before anything is saved.
func (s ModelService) Check() error {
	u, err := url.Parse(s.BaseURL)
	if err != nil || (u.Scheme != "https" && u.Scheme != "http") || u.Host == "" {
		return fmt.Errorf("claude: the model service address must be an http(s) URL, not %q", s.BaseURL)
	}
	if s.Token == "" {
		return errors.New("claude: the model service needs a key")
	}
	if s.Model == "" || strings.ContainsAny(s.Model, " \t\r\n") {
		return fmt.Errorf("claude: %q is not a model name", s.Model)
	}
	return nil
}

// SaveModelService keeps the service for this machine's sessions, readable by
// its owner alone.
func SaveModelService(loginDir string, s ModelService) error {
	if err := s.Check(); err != nil {
		return err
	}
	data, err := json.Marshal(s)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(loginDir, 0o700); err != nil {
		return fmt.Errorf("claude: %w", err)
	}
	temporary, err := os.CreateTemp(loginDir, ".model-service-*")
	if err != nil {
		return fmt.Errorf("claude: %w", err)
	}
	name := temporary.Name()
	if _, err := temporary.Write(data); err != nil {
		temporary.Close()
		os.Remove(name)
		return fmt.Errorf("claude: %w", err)
	}
	if err := temporary.Close(); err != nil {
		os.Remove(name)
		return fmt.Errorf("claude: %w", err)
	}
	if err := os.Chmod(name, 0o600); err != nil {
		os.Remove(name)
		return fmt.Errorf("claude: %w", err)
	}
	if err := os.Rename(name, filepath.Join(loginDir, serviceFile)); err != nil {
		os.Remove(name)
		return fmt.Errorf("claude: %w", err)
	}
	return nil
}

// LoadModelService is the service set on this machine, or nil when there is
// none and the sessions use the login.
func LoadModelService(loginDir string) (*ModelService, error) {
	data, err := os.ReadFile(filepath.Join(loginDir, serviceFile))
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("claude: %w", err)
	}
	var s ModelService
	if err := json.Unmarshal(data, &s); err != nil {
		return nil, fmt.Errorf("claude: the model service file is unreadable: %w", err)
	}
	return &s, nil
}

// ForgetModelService goes back to the login for this machine's sessions.
func ForgetModelService(loginDir string) error {
	err := os.Remove(filepath.Join(loginDir, serviceFile))
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return fmt.Errorf("claude: %w", err)
	}
	return nil
}
