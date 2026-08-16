package pptagent

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	"agent-task-platform/apps/api/internal/modules/auth"
	"agent-task-platform/apps/api/internal/shared/actor"
	"agent-task-platform/apps/api/internal/transport/http/middleware"

	"github.com/gin-gonic/gin"
	"github.com/gorilla/websocket"
)

func TestDecodeWSClientCommandUsesCanonicalEnvelope(t *testing.T) {
	command, protocolErr := decodeWSClientCommand([]byte(`{
		"schema_version":"1",
		"type":"run.subscribe",
		"request_id":"request-1",
		"payload":{"run_id":" RUN-1 ","after":7}
	}`))
	if protocolErr != nil {
		t.Fatal(protocolErr)
	}
	if command.Type != wsCommandRunSubscribe || command.RequestID != "request-1" || command.RunID != "run-1" || command.After != 7 {
		t.Fatalf("unexpected command: %#v", command)
	}

	tests := []struct {
		name string
		data string
		code string
	}{
		{name: "missing request id", data: `{"schema_version":"1","type":"ping","payload":{}}`, code: "invalid_command"},
		{name: "wrong schema", data: `{"schema_version":"2","type":"ping","request_id":"r1","payload":{}}`, code: "unsupported_schema_version"},
		{name: "business field outside payload", data: `{"schema_version":"1","type":"run.cancel","request_id":"r1","run_id":"run-1","payload":{}}`, code: "invalid_message"},
		{name: "missing run id", data: `{"schema_version":"1","type":"run.cancel","request_id":"r1","payload":{}}`, code: "invalid_command"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			_, protocolErr := decodeWSClientCommand([]byte(test.data))
			if protocolErr == nil || protocolErr.Code != test.code {
				t.Fatalf("protocol error = %#v, want code %q", protocolErr, test.code)
			}
		})
	}
}

func TestWebSocketSubscribeReplaysThenStreamsAndTerminalKeepsSocketOpen(t *testing.T) {
	revision := "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
	service := newWSTestService(&Task{
		SchemaVersion: CurrentSchemaVersion,
		ThreadID:      "thread-1",
		RunID:         "run-1",
		TaskID:        "run-1",
		Status:        TaskRunning,
		Stage:         StagePlanning,
	})
	service.replay["run-1"] = []AgentEvent{
		{RunID: "run-1", TaskID: "run-1", Sequence: 1, Type: EventTaskStarted, Stage: StagePreparing, Message: "started"},
		{RunID: "run-1", TaskID: "run-1", Sequence: 2, Revision: revision, Type: EventStepProgress, Stage: StagePlanning, Message: "planning"},
	}
	server := newWSTestServer(service)
	defer server.Close()
	connection := dialWSTestClient(t, server, nil)
	defer closeWSTestClient(connection)

	ready := readWSServerEnvelope(t, connection)
	if ready.Type != wsTypeConnectionReady || ready.RequestID != "" {
		t.Fatalf("unexpected ready frame: %#v", ready)
	}
	readyPayload := decodeWSPayload[wsConnectionReadyPayload](t, ready)
	if readyPayload.ConnectionID == "" || readyPayload.SchemaVersion != CurrentSchemaVersion {
		t.Fatalf("unexpected ready payload: %#v", readyPayload)
	}

	writeWSClientCommand(t, connection, wsCommandRunSubscribe, "subscribe-1", map[string]any{"run_id": "run-1", "after": 1})
	ack := readWSServerEnvelope(t, connection)
	if ack.Type != wsTypeCommandAck || ack.RequestID != "subscribe-1" {
		t.Fatalf("unexpected ack: %#v", ack)
	}
	snapshot := readWSServerEnvelope(t, connection)
	if snapshot.Type != wsTypeRunSnapshot || snapshot.RequestID != "subscribe-1" {
		t.Fatalf("unexpected snapshot: %#v", snapshot)
	}
	if task := decodeWSPayload[Task](t, snapshot); task.RunID != "run-1" {
		t.Fatalf("unexpected task snapshot: %#v", task)
	}
	replayed := readWSServerEnvelope(t, connection)
	if replayed.Type != wsTypeRunEvent || replayed.RequestID != "subscribe-1" {
		t.Fatalf("unexpected replay frame: %#v", replayed)
	}
	if event := decodeWSPayload[AgentEvent](t, replayed); event.Sequence != 2 || event.Revision != revision {
		t.Fatalf("replayed event = %#v, want sequence 2 and revision %q", event, revision)
	}

	service.emit("run-1", AgentEvent{
		RunID: "run-1", TaskID: "run-1", Sequence: 3, Type: EventTaskFailed, Stage: StagePlanning, Message: "failed",
		Error: &ErrorDetail{Code: "FAILED", Message: "failed"},
	})
	terminal := readWSServerEnvelope(t, connection)
	if terminal.Type != wsTypeRunEvent || !decodeWSPayload[AgentEvent](t, terminal).Terminal() {
		t.Fatalf("unexpected terminal frame: %#v", terminal)
	}

	writeWSClientCommand(t, connection, wsCommandPing, "ping-after-terminal", map[string]any{"nonce": "n-1"})
	pong := readWSServerEnvelope(t, connection)
	if pong.Type != wsTypePong || pong.RequestID != "ping-after-terminal" {
		t.Fatalf("terminal event closed the socket: %#v", pong)
	}
	if payload := decodeWSPayload[map[string]any](t, pong); payload["nonce"] != "n-1" {
		t.Fatalf("pong did not echo payload: %#v", payload)
	}
}

func TestWebSocketMultiplexesRunsAndReplacesDuplicateSubscription(t *testing.T) {
	service := newWSTestService(
		&Task{SchemaVersion: CurrentSchemaVersion, ThreadID: "thread-1", RunID: "run-1", TaskID: "run-1", Status: TaskRunning},
		&Task{SchemaVersion: CurrentSchemaVersion, ThreadID: "thread-2", RunID: "run-2", TaskID: "run-2", Status: TaskRunning},
	)
	server := newWSTestServer(service)
	defer server.Close()
	connection := dialWSTestClient(t, server, nil)
	defer closeWSTestClient(connection)
	_ = readWSServerEnvelope(t, connection)

	writeWSClientCommand(t, connection, wsCommandRunSubscribe, "subscribe-1", map[string]any{"run_id": "run-1"})
	assertWSFrameTypes(t, connection, wsTypeCommandAck, wsTypeRunSnapshot)
	writeWSClientCommand(t, connection, wsCommandRunSubscribe, "subscribe-2", map[string]any{"run_id": "run-2"})
	assertWSFrameTypes(t, connection, wsTypeCommandAck, wsTypeRunSnapshot)

	service.emit("run-2", AgentEvent{RunID: "run-2", TaskID: "run-2", Sequence: 1, Type: EventStepProgress, Stage: StagePlanning, Message: "run 2"})
	service.emit("run-1", AgentEvent{RunID: "run-1", TaskID: "run-1", Sequence: 1, Type: EventStepProgress, Stage: StagePlanning, Message: "run 1"})
	seen := map[string]string{}
	for range 2 {
		frame := readWSServerEnvelope(t, connection)
		if frame.Type != wsTypeRunEvent {
			t.Fatalf("unexpected multiplex frame: %#v", frame)
		}
		event := decodeWSPayload[AgentEvent](t, frame)
		seen[event.RunID] = frame.RequestID
	}
	if seen["run-1"] != "subscribe-1" || seen["run-2"] != "subscribe-2" {
		t.Fatalf("events were not correlated to their subscriptions: %#v", seen)
	}

	writeWSClientCommand(t, connection, wsCommandRunSubscribe, "subscribe-1b", map[string]any{"run_id": "run-1", "after": 1})
	assertWSFrameTypes(t, connection, wsTypeCommandAck, wsTypeRunSnapshot)
	waitForWSCondition(t, func() bool { return service.closedSubscriptions("run-1") >= 1 }, "old run-1 subscription was not closed")
	service.emit("run-1", AgentEvent{RunID: "run-1", TaskID: "run-1", Sequence: 2, Type: EventStepProgress, Stage: StagePlanning, Message: "replacement"})
	replaced := readWSServerEnvelope(t, connection)
	if replaced.Type != wsTypeRunEvent || replaced.RequestID != "subscribe-1b" || decodeWSPayload[AgentEvent](t, replaced).Sequence != 2 {
		t.Fatalf("replacement subscription did not own the event: %#v", replaced)
	}

	writeWSClientCommand(t, connection, wsCommandRunUnsubscribe, "unsubscribe-1", map[string]any{"run_id": "run-1"})
	unsubscribed := readWSServerEnvelope(t, connection)
	if unsubscribed.Type != wsTypeCommandAck || unsubscribed.RequestID != "unsubscribe-1" {
		t.Fatalf("unexpected unsubscribe ack: %#v", unsubscribed)
	}
	service.emit("run-1", AgentEvent{RunID: "run-1", TaskID: "run-1", Sequence: 3, Type: EventStepProgress, Stage: StagePlanning, Message: "stale"})
	writeWSClientCommand(t, connection, wsCommandPing, "ping-1", map[string]any{})
	if frame := readWSServerEnvelope(t, connection); frame.Type != wsTypePong || frame.RequestID != "ping-1" {
		t.Fatalf("unsubscribed run emitted a stale frame: %#v", frame)
	}
}

func TestWebSocketCancelIsExplicitAndDisconnectDoesNotCancelRun(t *testing.T) {
	t.Run("cancel command", func(t *testing.T) {
		service := newWSTestService(&Task{SchemaVersion: CurrentSchemaVersion, ThreadID: "thread-1", RunID: "run-1", TaskID: "run-1", Status: TaskRunning})
		server := newWSTestServer(service)
		defer server.Close()
		connection := dialWSTestClient(t, server, nil)
		defer closeWSTestClient(connection)
		_ = readWSServerEnvelope(t, connection)

		writeWSClientCommand(t, connection, wsCommandRunCancel, "cancel-1", map[string]any{"run_id": "run-1"})
		ack := readWSServerEnvelope(t, connection)
		snapshot := readWSServerEnvelope(t, connection)
		if ack.Type != wsTypeCommandAck || ack.RequestID != "cancel-1" || snapshot.Type != wsTypeRunSnapshot {
			t.Fatalf("unexpected cancel frames: ack=%#v snapshot=%#v", ack, snapshot)
		}
		if task := decodeWSPayload[Task](t, snapshot); task.Status != TaskCancelled {
			t.Fatalf("cancel snapshot status = %q", task.Status)
		}
		if service.cancelCount() != 1 {
			t.Fatalf("cancel calls = %d, want 1", service.cancelCount())
		}
	})

	t.Run("disconnect", func(t *testing.T) {
		service := newWSTestService(&Task{SchemaVersion: CurrentSchemaVersion, ThreadID: "thread-1", RunID: "run-1", TaskID: "run-1", Status: TaskRunning})
		server := newWSTestServer(service)
		connection := dialWSTestClient(t, server, nil)
		_ = readWSServerEnvelope(t, connection)
		writeWSClientCommand(t, connection, wsCommandRunSubscribe, "subscribe-1", map[string]any{"run_id": "run-1"})
		assertWSFrameTypes(t, connection, wsTypeCommandAck, wsTypeRunSnapshot)

		_ = connection.Close()
		waitForWSCondition(t, func() bool { return service.closedSubscriptions("run-1") == 1 }, "disconnect did not release its subscription")
		if service.cancelCount() != 0 {
			t.Fatalf("disconnect cancelled the run %d times", service.cancelCount())
		}
		server.Close()
	})
}

func TestWebSocketRejectsCrossOriginAndSelectsOnlyApplicationProtocol(t *testing.T) {
	service := newWSTestService()
	server := newWSTestServer(service)
	defer server.Close()
	dialer := websocket.Dialer{Subprotocols: []string{middleware.AgentTaskWebSocketProtocol, "bearer.must-not-echo"}}

	badHeader := http.Header{"Origin": []string{"https://untrusted.example"}}
	connection, response, err := dialer.Dial(wsTestURL(server), badHeader)
	if connection != nil {
		_ = connection.Close()
	}
	if err == nil || response == nil || response.StatusCode != http.StatusForbidden {
		t.Fatalf("cross-origin dial err=%v response=%v", err, response)
	}
	_ = response.Body.Close()

	goodHeader := http.Header{"Origin": []string{server.URL}}
	connection, response, err = dialer.Dial(wsTestURL(server), goodHeader)
	if err != nil {
		if response != nil {
			_ = response.Body.Close()
		}
		t.Fatal(err)
	}
	defer closeWSTestClient(connection)
	if connection.Subprotocol() != middleware.AgentTaskWebSocketProtocol {
		t.Fatalf("selected subprotocol = %q", connection.Subprotocol())
	}
	if strings.Contains(connection.Subprotocol(), "bearer") {
		t.Fatalf("credential subprotocol was echoed: %q", connection.Subprotocol())
	}
	_ = readWSServerEnvelope(t, connection)
}

func TestWebSocketRejectsDuplicateRequestIDAndClosesOnFullSendQueue(t *testing.T) {
	service := newWSTestService()
	server := newWSTestServer(service)
	defer server.Close()
	connection := dialWSTestClient(t, server, nil)
	defer closeWSTestClient(connection)
	_ = readWSServerEnvelope(t, connection)

	writeWSClientCommand(t, connection, wsCommandPing, "duplicate", map[string]any{})
	if frame := readWSServerEnvelope(t, connection); frame.Type != wsTypePong {
		t.Fatalf("first request failed: %#v", frame)
	}
	writeWSClientCommand(t, connection, wsCommandPing, "duplicate", map[string]any{})
	errorFrame := readWSServerEnvelope(t, connection)
	if errorFrame.Type != wsTypeCommandError || errorFrame.RequestID != "duplicate" {
		t.Fatalf("unexpected duplicate response: %#v", errorFrame)
	}
	if payload := decodeWSPayload[wsCommandErrorPayload](t, errorFrame); payload.Code != "duplicate_request_id" {
		t.Fatalf("duplicate error payload: %#v", payload)
	}

	ctx, cancel := context.WithCancel(context.Background())
	session := &wsSession{ctx: ctx, cancel: cancel, send: make(chan wsOutbound, 1)}
	session.send <- wsOutbound{envelope: &wsServerEnvelope{}}
	if session.enqueue(wsOutbound{envelope: &wsServerEnvelope{}}) {
		t.Fatal("full queue accepted another message")
	}
	if session.ctx.Err() == nil {
		t.Fatal("full queue did not cancel the connection")
	}
	code, _ := session.closeDetails()
	if code != websocket.CloseTryAgainLater {
		t.Fatalf("close code = %d, want %d", code, websocket.CloseTryAgainLater)
	}
}

func TestWebSocketCursorAheadReturnsWatermark(t *testing.T) {
	service := newWSTestService(&Task{
		SchemaVersion:     CurrentSchemaVersion,
		ThreadID:          "thread-1",
		RunID:             "run-1",
		TaskID:            "run-1",
		Status:            TaskRunning,
		LastEventSequence: 3,
	})
	service.subscribeErr = ErrEventCursorAhead
	server := newWSTestServer(service)
	defer server.Close()
	connection := dialWSTestClient(t, server, nil)
	defer closeWSTestClient(connection)
	_ = readWSServerEnvelope(t, connection)

	writeWSClientCommand(t, connection, wsCommandRunSubscribe, "cursor-ahead", map[string]any{"run_id": "run-1", "after": 9})
	frame := readWSServerEnvelope(t, connection)
	if frame.Type != wsTypeCommandError || frame.RequestID != "cursor-ahead" {
		t.Fatalf("unexpected cursor response: %#v", frame)
	}
	payload := decodeWSPayload[wsCommandErrorPayload](t, frame)
	if payload.Code != "event_cursor_ahead" || !payload.Retryable || payload.LastEventSequence == nil || *payload.LastEventSequence != 3 {
		t.Fatalf("unexpected cursor error payload: %#v", payload)
	}
}

func TestWebSocketRunNotReadyIsRetryable(t *testing.T) {
	service := newWSTestService(&Task{RunID: "run-1", TaskID: "run-1", Status: TaskRunning})
	service.cancelErr = ErrTaskNotReady
	server := newWSTestServer(service)
	defer server.Close()
	connection := dialWSTestClient(t, server, nil)
	defer closeWSTestClient(connection)
	_ = readWSServerEnvelope(t, connection)

	writeWSClientCommand(t, connection, wsCommandRunCancel, "cancel-not-ready", map[string]any{"run_id": "run-1"})
	frame := readWSServerEnvelope(t, connection)
	if frame.Type != wsTypeCommandError {
		t.Fatalf("unexpected cancel response: %#v", frame)
	}
	payload := decodeWSPayload[wsCommandErrorPayload](t, frame)
	if payload.Code != "run_not_ready" || !payload.Retryable {
		t.Fatalf("unexpected not-ready error payload: %#v", payload)
	}
}

func TestWebSocketInterruptedRunningSourceForcesResumableReconnect(t *testing.T) {
	service := newWSTestService(&Task{
		SchemaVersion: CurrentSchemaVersion,
		ThreadID:      "thread-1",
		RunID:         "run-1",
		TaskID:        "run-1",
		Status:        TaskRunning,
	})
	server := newWSTestServer(service)
	defer server.Close()
	connection := dialWSTestClient(t, server, nil)
	defer closeWSTestClient(connection)
	_ = readWSServerEnvelope(t, connection)
	writeWSClientCommand(t, connection, wsCommandRunSubscribe, "subscribe-1", map[string]any{"run_id": "run-1"})
	assertWSFrameTypes(t, connection, wsTypeCommandAck, wsTypeRunSnapshot)

	service.interrupt("run-1")
	if err := connection.SetReadDeadline(time.Now().Add(2 * time.Second)); err != nil {
		t.Fatal(err)
	}
	_, _, err := connection.ReadMessage()
	var closeError *websocket.CloseError
	if !errors.As(err, &closeError) || closeError.Code != websocket.CloseTryAgainLater {
		t.Fatalf("interrupted source close error = %v, want websocket 1013", err)
	}
}

func TestWebSocketClosedTerminalSourceRequiresCursorAtWatermark(t *testing.T) {
	newTerminalService := func() *wsTestService {
		return newWSTestService(&Task{
			SchemaVersion:     CurrentSchemaVersion,
			ThreadID:          "thread-1",
			RunID:             "run-1",
			TaskID:            "run-1",
			Status:            TaskSucceeded,
			LastEventSequence: 3,
		})
	}

	t.Run("caught up", func(t *testing.T) {
		server := newWSTestServer(newTerminalService())
		defer server.Close()
		connection := dialWSTestClient(t, server, nil)
		defer closeWSTestClient(connection)
		_ = readWSServerEnvelope(t, connection)
		writeWSClientCommand(t, connection, wsCommandRunSubscribe, "subscribe-1", map[string]any{"run_id": "run-1", "after": 3})
		assertWSFrameTypes(t, connection, wsTypeCommandAck, wsTypeRunSnapshot)
		writeWSClientCommand(t, connection, wsCommandPing, "ping-1", map[string]any{})
		if frame := readWSServerEnvelope(t, connection); frame.Type != wsTypePong {
			t.Fatalf("caught-up terminal subscription closed the socket: %#v", frame)
		}
	})

	t.Run("missing terminal event", func(t *testing.T) {
		server := newWSTestServer(newTerminalService())
		defer server.Close()
		connection := dialWSTestClient(t, server, nil)
		defer closeWSTestClient(connection)
		_ = readWSServerEnvelope(t, connection)
		writeWSClientCommand(t, connection, wsCommandRunSubscribe, "subscribe-1", map[string]any{"run_id": "run-1", "after": 2})
		if err := connection.SetReadDeadline(time.Now().Add(2 * time.Second)); err != nil {
			t.Fatal(err)
		}
		for {
			_, _, err := connection.ReadMessage()
			if err == nil {
				continue
			}
			var closeError *websocket.CloseError
			if !errors.As(err, &closeError) || closeError.Code != websocket.CloseTryAgainLater {
				t.Fatalf("missing terminal event close error = %v, want websocket 1013", err)
			}
			break
		}
	})
}

func TestWebSocketConnectionStateIsBounded(t *testing.T) {
	t.Run("request id replay window", func(t *testing.T) {
		session := &wsSession{requestIDs: make(map[string]struct{})}
		for index := 0; index < webSocketRequestIDWindow; index++ {
			if !session.reserveRequestID(fmt.Sprintf("request-%d", index)) {
				t.Fatalf("request id %d was unexpectedly rejected", index)
			}
		}
		if session.reserveRequestID(fmt.Sprintf("request-%d", webSocketRequestIDWindow-1)) {
			t.Fatal("duplicate request id inside the window was accepted")
		}
		if !session.reserveRequestID("request-next") || !session.reserveRequestID("request-0") {
			t.Fatal("oldest request id was not evicted from the bounded window")
		}
		if len(session.requestIDs) != webSocketRequestIDWindow || len(session.requestIDOrder) != webSocketRequestIDWindow {
			t.Fatalf("request id state grew past its bound: map=%d order=%d", len(session.requestIDs), len(session.requestIDOrder))
		}
	})

	t.Run("active subscription limit", func(t *testing.T) {
		ctx, cancel := context.WithCancel(context.Background())
		defer cancel()
		session := &wsSession{
			ctx:           ctx,
			cancel:        cancel,
			service:       newWSTestService(),
			send:          make(chan wsOutbound, 1),
			subscriptions: make(map[string]*wsRunSubscription),
		}
		for index := 0; index < webSocketMaxSubscriptions; index++ {
			session.subscriptions[fmt.Sprintf("run-%d", index)] = nil
		}
		session.handleSubscribe(wsClientCommand{Type: wsCommandRunSubscribe, RequestID: "request-overflow", RunID: "run-overflow"})
		outbound := <-session.send
		if outbound.envelope == nil || outbound.envelope.Type != wsTypeCommandError {
			t.Fatalf("unexpected subscription limit response: %#v", outbound)
		}
		payload, ok := outbound.envelope.Payload.(wsCommandErrorPayload)
		if !ok || payload.Code != "subscription_limit_exceeded" {
			t.Fatalf("unexpected subscription limit payload: %#v", outbound.envelope.Payload)
		}
	})
}

type wsDecodedServerEnvelope struct {
	SchemaVersion string          `json:"schema_version"`
	Type          string          `json:"type"`
	RequestID     string          `json:"request_id,omitempty"`
	CreatedAt     time.Time       `json:"created_at"`
	Payload       json.RawMessage `json:"payload"`
}

func newWSTestServer(service taskService) *httptest.Server {
	gin.SetMode(gin.TestMode)
	engine := gin.New()
	handler := NewHandler(service)
	engine.GET("/ws", func(c *gin.Context) {
		c.Set(auth.ContextKeyClaims, &auth.Claims{UserID: 7, Username: "tester"})
		c.Next()
	}, handler.WebSocket)
	return httptest.NewServer(engine)
}

func wsTestURL(server *httptest.Server) string {
	return "ws" + strings.TrimPrefix(server.URL, "http") + "/ws"
}

func dialWSTestClient(t *testing.T, server *httptest.Server, header http.Header) *websocket.Conn {
	t.Helper()
	dialer := websocket.Dialer{Subprotocols: []string{middleware.AgentTaskWebSocketProtocol}}
	connection, response, err := dialer.Dial(wsTestURL(server), header)
	if err != nil {
		if response != nil {
			_ = response.Body.Close()
		}
		t.Fatal(err)
	}
	if connection.Subprotocol() != middleware.AgentTaskWebSocketProtocol {
		_ = connection.Close()
		t.Fatalf("selected subprotocol = %q", connection.Subprotocol())
	}
	return connection
}

func closeWSTestClient(connection *websocket.Conn) {
	if connection == nil {
		return
	}
	_ = connection.WriteControl(websocket.CloseMessage, websocket.FormatCloseMessage(websocket.CloseNormalClosure, ""), time.Now().Add(time.Second))
	_ = connection.Close()
}

func writeWSClientCommand(t *testing.T, connection *websocket.Conn, commandType, requestID string, payload any) {
	t.Helper()
	if err := connection.WriteJSON(map[string]any{
		"schema_version": CurrentSchemaVersion,
		"type":           commandType,
		"request_id":     requestID,
		"payload":        payload,
	}); err != nil {
		t.Fatal(err)
	}
}

func readWSServerEnvelope(t *testing.T, connection *websocket.Conn) wsDecodedServerEnvelope {
	t.Helper()
	if err := connection.SetReadDeadline(time.Now().Add(2 * time.Second)); err != nil {
		t.Fatal(err)
	}
	_, data, err := connection.ReadMessage()
	if err != nil {
		t.Fatal(err)
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(data, &fields); err != nil {
		t.Fatalf("decode server frame: %v; frame=%s", err, data)
	}
	for field := range fields {
		switch field {
		case "schema_version", "type", "request_id", "created_at", "payload":
		default:
			t.Fatalf("business field %q escaped the canonical envelope: %s", field, data)
		}
	}
	if _, ok := fields["payload"]; !ok {
		t.Fatalf("server frame omitted payload: %s", data)
	}
	var envelope wsDecodedServerEnvelope
	if err := json.Unmarshal(data, &envelope); err != nil {
		t.Fatalf("decode server envelope: %v; frame=%s", err, data)
	}
	if envelope.SchemaVersion != CurrentSchemaVersion || envelope.Type == "" || envelope.CreatedAt.IsZero() {
		t.Fatalf("incomplete server envelope: %#v", envelope)
	}
	return envelope
}

func decodeWSPayload[T any](t *testing.T, envelope wsDecodedServerEnvelope) T {
	t.Helper()
	var payload T
	if err := json.Unmarshal(envelope.Payload, &payload); err != nil {
		t.Fatalf("decode %s payload: %v; payload=%s", envelope.Type, err, envelope.Payload)
	}
	return payload
}

func assertWSFrameTypes(t *testing.T, connection *websocket.Conn, expected ...string) {
	t.Helper()
	for _, messageType := range expected {
		if frame := readWSServerEnvelope(t, connection); frame.Type != messageType {
			t.Fatalf("frame type = %q, want %q", frame.Type, messageType)
		}
	}
}

func waitForWSCondition(t *testing.T, condition func() bool, message string) {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		if condition() {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatal(message)
}

type wsTestFeed struct {
	events chan AgentEvent
	closed bool
}

type wsTestService struct {
	mu            sync.Mutex
	tasks         map[string]*Task
	replay        map[string][]AgentEvent
	feeds         map[string][]*wsTestFeed
	closed        map[string]int
	cancelledRuns []string
	subscribeErr  error
	cancelErr     error
}

func newWSTestService(tasks ...*Task) *wsTestService {
	service := &wsTestService{
		tasks:  make(map[string]*Task),
		replay: make(map[string][]AgentEvent),
		feeds:  make(map[string][]*wsTestFeed),
		closed: make(map[string]int),
	}
	for _, task := range tasks {
		service.tasks[task.RunID] = cloneTask(task)
	}
	return service
}

func (service *wsTestService) CreateTask(context.Context, actor.User, CreateTaskRequest) (*Task, error) {
	return nil, errors.New("not implemented")
}

func (service *wsTestService) GetTask(_ context.Context, _ int64, runID string) (*Task, error) {
	service.mu.Lock()
	defer service.mu.Unlock()
	task := service.tasks[runID]
	if task == nil {
		return nil, ErrTaskNotFound
	}
	return cloneTask(task), nil
}

func (service *wsTestService) ListTasks(context.Context, int64, string, int) ([]*Task, error) {
	return nil, errors.New("not implemented")
}

func (service *wsTestService) SubscribeTask(_ context.Context, _ int64, runID string, _ uint64) (*Subscription, error) {
	service.mu.Lock()
	defer service.mu.Unlock()
	if service.subscribeErr != nil {
		return nil, service.subscribeErr
	}
	if service.tasks[runID] == nil {
		return nil, ErrTaskNotFound
	}
	feed := &wsTestFeed{events: make(chan AgentEvent, 16)}
	if service.tasks[runID].Status.Terminal() {
		feed.closed = true
		close(feed.events)
	}
	service.feeds[runID] = append(service.feeds[runID], feed)
	var once sync.Once
	return &Subscription{
		Replay: append([]AgentEvent(nil), service.replay[runID]...),
		Events: feed.events,
		Close: func() {
			once.Do(func() {
				service.mu.Lock()
				defer service.mu.Unlock()
				feed.closed = true
				service.closed[runID]++
			})
		},
	}, nil
}

func (service *wsTestService) interrupt(runID string) {
	service.mu.Lock()
	defer service.mu.Unlock()
	for _, feed := range service.feeds[runID] {
		if feed.closed {
			continue
		}
		feed.closed = true
		close(feed.events)
	}
}

func (service *wsTestService) CancelTask(_ context.Context, _ int64, runID string) (*Task, error) {
	service.mu.Lock()
	defer service.mu.Unlock()
	if service.cancelErr != nil {
		return nil, service.cancelErr
	}
	task := service.tasks[runID]
	if task == nil {
		return nil, ErrTaskNotFound
	}
	service.cancelledRuns = append(service.cancelledRuns, runID)
	task.Status = TaskCancelled
	return cloneTask(task), nil
}

func (service *wsTestService) DeleteTask(context.Context, int64, string) error {
	return errors.New("not implemented")
}

func (service *wsTestService) OpenArtifact(context.Context, int64, string, string) (*os.File, error) {
	return nil, errors.New("not implemented")
}

func (service *wsTestService) emit(runID string, event AgentEvent) {
	service.mu.Lock()
	defer service.mu.Unlock()
	for _, feed := range service.feeds[runID] {
		if feed.closed {
			continue
		}
		feed.events <- event
	}
}

func (service *wsTestService) closedSubscriptions(runID string) int {
	service.mu.Lock()
	defer service.mu.Unlock()
	return service.closed[runID]
}

func (service *wsTestService) cancelCount() int {
	service.mu.Lock()
	defer service.mu.Unlock()
	return len(service.cancelledRuns)
}
