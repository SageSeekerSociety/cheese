// Package localapi runs the local, loopback-only HTTP server that the
// `cheese` CLI talks to (contract §6). It is a thin, transparent proxy onto
// the backend orchestrator's tool endpoints: schema discovery and tool
// invocation. Authorization happens entirely on the backend side.
package localapi

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"time"
)

// ToolProxy is the seam that forwards schema/call requests to the backend
// orchestrator. Splitting it out lets tests substitute a fake backend.
type ToolProxy interface {
	// Schema proxies GET /connector/tools/schema. It returns the raw
	// response body, the upstream status code, and any transport error.
	Schema(ctx context.Context) (body []byte, status int, err error)
	// Call proxies POST /connector/tools/call?tool=<name> with reqBody as
	// the request body. It returns the raw response body, the upstream
	// status code, and any transport error.
	Call(ctx context.Context, tool string, reqBody []byte) (body []byte, status int, err error)
}

// HTTPToolProxy is the default ToolProxy, forwarding to a backend HTTP base
// URL with the session token attached as X-Cheese-Session (contract §6).
type HTTPToolProxy struct {
	backendHTTPURL string
	sessionToken   string
	client         *http.Client
}

// NewHTTPToolProxy builds a proxy targeting backendHTTPURL, authenticating
// with sessionToken.
func NewHTTPToolProxy(backendHTTPURL, sessionToken string) *HTTPToolProxy {
	return &HTTPToolProxy{
		backendHTTPURL: backendHTTPURL,
		sessionToken:   sessionToken,
		client:         &http.Client{Timeout: 30 * time.Second},
	}
}

var _ ToolProxy = (*HTTPToolProxy)(nil)

func (p *HTTPToolProxy) Schema(ctx context.Context) ([]byte, int, error) {
	schemaURL := p.backendHTTPURL + "/connector/tools/schema"
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, schemaURL, nil)
	if err != nil {
		return nil, 0, err
	}
	req.Header.Set("X-Cheese-Session", p.sessionToken)
	return p.do(req)
}

func (p *HTTPToolProxy) Call(ctx context.Context, tool string, reqBody []byte) ([]byte, int, error) {
	callURL := fmt.Sprintf("%s/connector/tools/call?tool=%s", p.backendHTTPURL, url.QueryEscape(tool))
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, callURL, bytes.NewReader(reqBody))
	if err != nil {
		return nil, 0, err
	}
	req.Header.Set("X-Cheese-Session", p.sessionToken)
	req.Header.Set("Content-Type", "application/json")
	return p.do(req)
}

func (p *HTTPToolProxy) do(req *http.Request) ([]byte, int, error) {
	resp, err := p.client.Do(req)
	if err != nil {
		return nil, 0, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, resp.StatusCode, err
	}
	return body, resp.StatusCode, nil
}

// Server is the loopback HTTP server exposing /schema and /call.
type Server struct {
	addr   string
	proxy  ToolProxy
	server *http.Server
}

// New builds a Server bound to 127.0.0.1:port, proxying through proxy.
func New(port int, proxy ToolProxy) *Server {
	s := &Server{
		addr:  fmt.Sprintf("127.0.0.1:%d", port),
		proxy: proxy,
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/schema", s.handleSchema)
	mux.HandleFunc("/call", s.handleCall)
	s.server = &http.Server{
		Addr:    s.addr,
		Handler: mux,
	}
	return s
}

// Start begins listening in a background goroutine and returns immediately.
// Bind errors are returned synchronously; later runtime errors other than
// a clean shutdown are surfaced on errCh.
func (s *Server) Start() (errCh <-chan error, err error) {
	ln, err := net.Listen("tcp", s.addr)
	if err != nil {
		return nil, fmt.Errorf("localapi: listen %s: %w", s.addr, err)
	}

	ch := make(chan error, 1)
	go func() {
		serveErr := s.server.Serve(ln)
		if serveErr != nil && !errors.Is(serveErr, http.ErrServerClosed) {
			ch <- serveErr
		}
		close(ch)
	}()
	return ch, nil
}

// Shutdown gracefully stops the server.
func (s *Server) Shutdown(ctx context.Context) error {
	return s.server.Shutdown(ctx)
}

func (s *Server) handleSchema(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	body, status, err := s.proxy.Schema(r.Context())
	writeProxied(w, body, status, err)
}

func (s *Server) handleCall(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	tool := r.URL.Query().Get("tool")
	if tool == "" {
		http.Error(w, `{"ok":false,"error":"missing tool query parameter"}`, http.StatusBadRequest)
		return
	}
	reqBody, err := io.ReadAll(r.Body)
	if err != nil {
		http.Error(w, `{"ok":false,"error":"failed to read request body"}`, http.StatusBadRequest)
		return
	}
	body, status, callErr := s.proxy.Call(r.Context(), tool, reqBody)
	writeProxied(w, body, status, callErr)
}

func writeProxied(w http.ResponseWriter, body []byte, status int, err error) {
	if err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadGateway)
		_, _ = w.Write([]byte(fmt.Sprintf(`{"ok":false,"error":%q}`, err.Error())))
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write(body)
}
