package daemoncmd

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/SageSeekerSociety/cheese/cli/internal/config"
	"github.com/SageSeekerSociety/cheese/cli/internal/devenv"
	"github.com/SageSeekerSociety/cheese/cli/internal/service"
)

type registeredModelLogin struct {
	LoginID        string `json:"login_id"`
	Token          string `json:"token"`
	DeviceID       string `json:"device_id"`
	CredentialHost bool   `json:"credential_host"`
}

// registerModelLogin grants a credential its own team. The transport may be an
// existing enrollment, but adding a subscription never enrolls an execution device.
func registerModelLogin(cfgPath *string, server, ticket string) (string, error) {
	cfg, _ := config.Load(*cfgPath)
	if cfg != nil && cfg.Token != "" && cfg.Base != server {
		path, err := config.ReserveEnrollment()
		if err != nil {
			return "", err
		}
		*cfgPath, cfg = path, nil
	}
	if cfg == nil {
		cfg = &config.Config{Base: server}
	}
	name, err := os.Hostname()
	if err != nil {
		return "", err
	}
	body := map[string]string{"ticket": ticket, "transport_token": cfg.Token, "computer_name": name}
	var registered registeredModelLogin
	if err := modelLoginRequest(server+"/model-login/register", body, &registered); err != nil {
		return "", err
	}
	if registered.LoginID == "" || registered.Token == "" || registered.DeviceID == "" {
		return "", fmt.Errorf("model login: incomplete registration")
	}
	cfg.Base, cfg.Token, cfg.DeviceID = server, registered.Token, registered.DeviceID
	if err := config.Save(*cfgPath, cfg); err != nil {
		return "", err
	}
	if err := config.Activate(*cfgPath); err != nil {
		return "", err
	}
	return registered.LoginID, nil
}

func modelLoginRequest(url string, body, result any) error {
	data, err := json.Marshal(body)
	if err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(context.Background(), http.MethodPost, url, bytes.NewReader(data))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	client := &http.Client{Timeout: 100 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("model login: server returned HTTP %d", resp.StatusCode)
	}
	return json.NewDecoder(resp.Body).Decode(result)
}

func connectModelLogin(cfgPath string) error {
	cfg, err := config.Load(cfgPath)
	if err != nil {
		return err
	}
	if err := devenv.Ensure(context.Background(), cfg.Base, os.Stdout); err != nil {
		return err
	}
	if err := service.Control(cfgPath, "install"); err != nil {
		return err
	}
	if err := service.Control(cfgPath, "start"); err != nil {
		return err
	}
	var refreshed struct {
		Refreshed bool `json:"refreshed"`
	}
	return modelLoginRequest(strings.TrimRight(cfg.Base, "/")+"/model-login/refresh", map[string]string{"token": cfg.Token}, &refreshed)
}
