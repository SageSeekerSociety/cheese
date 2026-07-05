// Package client implements a thin HTTP client for talking to the cheesed
// local daemon API (contract §7): GET /schema and POST /call?tool=<name>.
package client

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// Daemon is the seam implemented by HTTPClient. It exists so callers (and
// tests) can substitute a fake daemon without depending on net/http.
type Daemon interface {
	// Schema fetches the tool schema document from the daemon's /schema
	// endpoint and returns the raw JSON bytes.
	Schema(ctx context.Context) ([]byte, error)

	// Call invokes a tool by name via the daemon's /call endpoint. argsJSON
	// is the raw JSON object to send as the request body (may be empty,
	// meaning "no arguments"). Returns the raw JSON response bytes.
	//
	// If the daemon reports a non-2xx HTTP status, Call returns a non-nil
	// error (callers should treat this as "the tool call failed") while
	// still returning any response body that was received, so the caller
	// can print diagnostic detail.
	Call(ctx context.Context, tool string, argsJSON []byte) ([]byte, error)
}

// HTTPClient is the default Daemon implementation, talking to cheesed's
// local HTTP server (contract §7).
type HTTPClient struct {
	BaseURL    string
	HTTPClient *http.Client
}

// New constructs an HTTPClient for the given base URL (e.g.
// "http://127.0.0.1:47615"). baseURL is normalized to have no trailing
// slash.
func New(baseURL string) *HTTPClient {
	return &HTTPClient{
		BaseURL:    strings.TrimRight(baseURL, "/"),
		HTTPClient: &http.Client{Timeout: 60 * time.Second},
	}
}

var _ Daemon = (*HTTPClient)(nil)

func (c *HTTPClient) Schema(ctx context.Context) ([]byte, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.BaseURL+"/schema", nil)
	if err != nil {
		return nil, fmt.Errorf("building schema request: %w", err)
	}
	return c.do(req)
}

func (c *HTTPClient) Call(ctx context.Context, tool string, argsJSON []byte) ([]byte, error) {
	if tool == "" {
		return nil, fmt.Errorf("tool name must not be empty")
	}
	var body io.Reader
	if len(argsJSON) > 0 {
		body = bytes.NewReader(argsJSON)
	}
	reqURL := fmt.Sprintf("%s/call?tool=%s", c.BaseURL, url.QueryEscape(tool))
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, reqURL, body)
	if err != nil {
		return nil, fmt.Errorf("building call request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	return c.do(req)
}

func (c *HTTPClient) do(req *http.Request) ([]byte, error) {
	resp, err := c.HTTPClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("contacting daemon at %s: %w", c.BaseURL, err)
	}
	defer resp.Body.Close()

	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("reading daemon response: %w", err)
	}

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return data, &StatusError{StatusCode: resp.StatusCode, Body: data}
	}
	return data, nil
}

// StatusError is returned when the daemon responds with a non-2xx status.
type StatusError struct {
	StatusCode int
	Body       []byte
}

func (e *StatusError) Error() string {
	msg := strings.TrimSpace(string(e.Body))
	if msg == "" {
		msg = "(empty body)"
	}
	return fmt.Sprintf("daemon returned HTTP %d: %s", e.StatusCode, msg)
}

// PrettyJSON re-indents raw JSON bytes for human-friendly output. If data is
// not valid JSON, it is returned unchanged.
func PrettyJSON(data []byte) []byte {
	var v interface{}
	if err := json.Unmarshal(data, &v); err != nil {
		return data
	}
	out, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return data
	}
	return out
}
