package backendlink

// Outbound TEXT control messages (cheesed -> backend, contract §5).

type helloMsg struct {
	T        string `json:"t"`
	Session  string `json:"session"`
	Protocol int    `json:"protocol"`
	Agent    string `json:"agent"`
	Cols     int    `json:"cols"`
	Rows     int    `json:"rows"`
}

type heartbeatMsg struct {
	T string `json:"t"`
}

type statusMsg struct {
	T      string `json:"t"`
	Phase  string `json:"phase"`
	Detail string `json:"detail"`
}

// inboundEnvelope is the union of all TEXT control messages the backend may
// send (contract §5): takeover / drive / resize / viewer. Fields are
// pointers where their presence is semantically meaningful (e.g. "on" must
// be distinguishable from "absent").
type inboundEnvelope struct {
	T       string   `json:"t"`
	On      *bool    `json:"on,omitempty"`
	Keys    []string `json:"keys,omitempty"`
	Cols    int      `json:"cols,omitempty"`
	Rows    int      `json:"rows,omitempty"`
	Present *bool    `json:"present,omitempty"`
}
