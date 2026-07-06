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

// inboundEnvelope is the union of the TEXT control messages a thin relay
// reacts to (architecture §5): resize (terminal geometry) and viewer
// (presence -> spin the on-demand view up/down). Fields are pointers where
// presence is semantically meaningful (e.g. "present" must be
// distinguishable from "absent").
type inboundEnvelope struct {
	T       string `json:"t"`
	Cols    int    `json:"cols,omitempty"`
	Rows    int    `json:"rows,omitempty"`
	Present *bool  `json:"present,omitempty"`
}
