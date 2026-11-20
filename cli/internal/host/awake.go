package host

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/SageSeekerSociety/cheese/cli/internal/awake"
	"github.com/SageSeekerSociety/cheese/cli/internal/config"
)

// Runs for the connector's lifetime. Failed reads retain the last known work
// count: losing the network must not put an in-flight local task to sleep.
func (h *Host) startSleep(ctx context.Context) {
	h.sleepMu.Lock()
	defer h.sleepMu.Unlock()
	if h.sleepCancel != nil {
		return
	}
	ctx, h.sleepCancel = context.WithCancel(ctx)
	done := make(chan struct{})
	h.sleepDone = done
	go func() { defer close(done); h.preventSleep(ctx, awake.AcquireSystem, 5*time.Second) }()
}

func (h *Host) stopSleep() {
	h.sleepMu.Lock()
	defer h.sleepMu.Unlock()
	if h.sleepCancel != nil {
		h.sleepCancel()
		<-h.sleepDone
		h.sleepCancel, h.sleepDone = nil, nil
	}
}

func (h *Host) preventSleep(ctx context.Context, acquire awake.Acquire, interval time.Duration) {
	guard := awake.New(acquire)
	defer guard.Close()
	client := &http.Client{Timeout: 10 * time.Second}
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	count := 0
	for {
		m, err := config.LoadMachine(config.MachinePath())
		if err != nil {
			fmt.Fprintf(os.Stderr, "cheese: sleep preference: %v\n", err)
		} else {
			if m.PreventSleep {
				n, err := runningSessions(ctx, client, h.base, h.token)
				if err == nil {
					count = n
				} else {
					fmt.Fprintf(os.Stderr, "cheese: running sessions: %v\n", err)
				}
			}
			if err := guard.Update(m.PreventSleep, count); err != nil {
				fmt.Fprintf(os.Stderr, "cheese: prevent sleep: %v\n", err)
			}
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

func runningSessions(ctx context.Context, client *http.Client, base, token string) (int, error) {
	req, err := http.NewRequestWithContext(ctx, "GET", strings.TrimRight(base, "/")+"/device/running-sessions", nil)
	if err != nil {
		return 0, err
	}
	req.Header.Set("Authorization", "Bearer "+token)
	resp, err := client.Do(req)
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return 0, fmt.Errorf("HTTP %d", resp.StatusCode)
	}
	var out struct {
		Sessions []json.RawMessage `json:"sessions"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return 0, err
	}
	return len(out.Sessions), nil
}
