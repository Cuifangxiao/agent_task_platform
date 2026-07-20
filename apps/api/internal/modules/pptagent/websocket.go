package pptagent

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"
	"sync"
	"time"

	"agent-task-platform/apps/api/internal/transport/http/httpctx"
	"agent-task-platform/apps/api/internal/transport/http/middleware"
	"agent-task-platform/apps/api/internal/transport/http/response"

	"github.com/gin-gonic/gin"
	"github.com/gorilla/websocket"
)

const (
	webSocketMaxMessageBytes  = 64 << 10
	webSocketSendQueueSize    = 128
	webSocketReadTimeout      = 90 * time.Second
	webSocketWriteTimeout     = 10 * time.Second
	webSocketRequestIDWindow  = 4096
	webSocketMaxSubscriptions = 32
)

const (
	wsTypeConnectionReady = "connection.ready"
	wsTypeCommandAck      = "command.ack"
	wsTypeRunSnapshot     = "run.snapshot"
	wsTypeRunEvent        = "run.event"
	wsTypeCommandError    = "command.error"
	wsTypePong            = "pong"

	wsCommandRunSubscribe   = "run.subscribe"
	wsCommandRunUnsubscribe = "run.unsubscribe"
	wsCommandRunCancel      = "run.cancel"
	wsCommandPing           = "ping"
)

var pptWebSocketUpgrader = websocket.Upgrader{
	HandshakeTimeout: 10 * time.Second,
	ReadBufferSize:   4 << 10,
	WriteBufferSize:  4 << 10,
	Subprotocols:     []string{middleware.AgentTaskWebSocketProtocol},
	// CheckOrigin intentionally remains nil: Gorilla's secure default accepts
	// same-origin browser handshakes and rejects cross-origin upgrades.
}

type wsClientEnvelope struct {
	SchemaVersion string          `json:"schema_version"`
	Type          string          `json:"type"`
	RequestID     string          `json:"request_id,omitempty"`
	CreatedAt     *time.Time      `json:"created_at,omitempty"`
	Payload       json.RawMessage `json:"payload"`
}

type wsRunCommandPayload struct {
	RunID string `json:"run_id"`
	After uint64 `json:"after,omitempty"`
}

type wsClientCommand struct {
	Type        string
	RequestID   string
	RunID       string
	After       uint64
	PingPayload json.RawMessage
}

type wsServerEnvelope struct {
	SchemaVersion string    `json:"schema_version"`
	Type          string    `json:"type"`
	RequestID     string    `json:"request_id,omitempty"`
	CreatedAt     time.Time `json:"created_at"`
	Payload       any       `json:"payload"`
}

type wsConnectionReadyPayload struct {
	ConnectionID  string `json:"connection_id"`
	SchemaVersion string `json:"schema_version"`
}

type wsCommandAckPayload struct {
	Command string `json:"command"`
	RunID   string `json:"run_id,omitempty"`
}

type wsCommandErrorPayload struct {
	Code              string  `json:"code"`
	Message           string  `json:"message"`
	Command           string  `json:"command,omitempty"`
	RunID             string  `json:"run_id,omitempty"`
	Retryable         bool    `json:"retryable"`
	LastEventSequence *uint64 `json:"last_event_sequence,omitempty"`
}

type wsProtocolError struct {
	RequestID string
	Command   string
	RunID     string
	Code      string
	Message   string
}

func (err *wsProtocolError) Error() string {
	return err.Message
}

type wsOutbound struct {
	envelope    *wsServerEnvelope
	controlType int
	controlData []byte
}

type wsRunSubscription struct {
	ctx       context.Context
	cancel    context.CancelFunc
	source    *Subscription
	requestID string
	runID     string
	after     uint64
	stopOnce  sync.Once
}

func (subscription *wsRunSubscription) stop() {
	if subscription == nil {
		return
	}
	subscription.stopOnce.Do(func() {
		subscription.cancel()
		if subscription.source != nil && subscription.source.Close != nil {
			subscription.source.Close()
		}
	})
}

type wsSession struct {
	ctx             context.Context
	cancel          context.CancelFunc
	connection      *websocket.Conn
	service         taskService
	ownerID         int64
	connectionID    string
	send            chan wsOutbound
	writerDone      chan struct{}
	requestIDs      map[string]struct{} // Owned by the single reader goroutine.
	requestIDOrder  []string
	requestIDCursor int

	subscriptionsMu sync.Mutex
	subscriptions   map[string]*wsRunSubscription

	closeMu     sync.Mutex
	closeSet    bool
	closeCode   int
	closeReason string
}

func (h *Handler) WebSocket(c *gin.Context) {
	claims, ok := httpctx.Claims(c)
	if !ok {
		response.Internal(c, "missing auth claims")
		return
	}
	connectionID, err := randomUUID()
	if err != nil {
		response.InternalServerError(c)
		return
	}
	connection, err := pptWebSocketUpgrader.Upgrade(c.Writer, c.Request, nil)
	if err != nil {
		return
	}
	ctx, cancel := context.WithCancel(c.Request.Context())
	session := &wsSession{
		ctx:           ctx,
		cancel:        cancel,
		connection:    connection,
		service:       h.service,
		ownerID:       claims.UserID,
		connectionID:  connectionID,
		send:          make(chan wsOutbound, webSocketSendQueueSize),
		writerDone:    make(chan struct{}),
		requestIDs:    make(map[string]struct{}),
		subscriptions: make(map[string]*wsRunSubscription),
	}
	session.run()
}

func (session *wsSession) run() {
	session.connection.SetReadLimit(webSocketMaxMessageBytes)
	session.connection.SetCloseHandler(func(code int, _ string) error {
		if !validWebSocketCloseCode(code) {
			code = websocket.CloseNormalClosure
		}
		session.closeWith(code, "")
		return nil
	})
	session.connection.SetPingHandler(func(data string) error {
		if session.enqueue(wsOutbound{controlType: websocket.PongMessage, controlData: []byte(data)}) {
			return nil
		}
		return context.Canceled
	})

	go session.writeLoop()
	session.sendEnvelope(wsTypeConnectionReady, "", wsConnectionReadyPayload{
		ConnectionID:  session.connectionID,
		SchemaVersion: CurrentSchemaVersion,
	})

	readErr := session.readLoop()
	if readErr != nil {
		var closeError *websocket.CloseError
		if errors.As(readErr, &closeError) && validWebSocketCloseCode(closeError.Code) {
			session.closeWith(closeError.Code, "")
		} else {
			session.closeWith(websocket.CloseGoingAway, "connection closed")
		}
	} else {
		session.closeWith(websocket.CloseNormalClosure, "")
	}
	session.stopAllSubscriptions()
	<-session.writerDone
}

func (session *wsSession) readLoop() error {
	for {
		// Browser clients send an application-level ping every 25 seconds. A
		// finite read deadline releases half-open connections and their run
		// subscriptions when a network disappears without a close frame.
		if err := session.connection.SetReadDeadline(time.Now().Add(webSocketReadTimeout)); err != nil {
			return err
		}
		messageType, data, err := session.connection.ReadMessage()
		if err != nil {
			return err
		}
		if messageType != websocket.TextMessage {
			session.sendCommandError(&wsProtocolError{
				Code:    "unsupported_data_type",
				Message: "commands must use text JSON frames",
			})
			continue
		}
		command, protocolErr := decodeWSClientCommand(data)
		if protocolErr != nil {
			if protocolErr.RequestID != "" && !session.reserveRequestID(protocolErr.RequestID) {
				protocolErr = duplicateRequestIDError(protocolErr.RequestID, protocolErr.Command)
			}
			session.sendCommandError(protocolErr)
			continue
		}
		if !session.reserveRequestID(command.RequestID) {
			session.sendCommandError(duplicateRequestIDError(command.RequestID, command.Type))
			continue
		}
		session.handleCommand(command)
	}
}

func (session *wsSession) writeLoop() {
	defer close(session.writerDone)
	defer session.connection.Close()
	for {
		select {
		case <-session.ctx.Done():
			session.writeClose()
			return
		default:
		}

		select {
		case <-session.ctx.Done():
			session.writeClose()
			return
		case outbound := <-session.send:
			if err := session.connection.SetWriteDeadline(time.Now().Add(webSocketWriteTimeout)); err != nil {
				session.closeWith(websocket.CloseInternalServerErr, "write failed")
				return
			}
			var err error
			if outbound.envelope != nil {
				err = session.connection.WriteJSON(outbound.envelope)
			} else {
				err = session.connection.WriteControl(outbound.controlType, outbound.controlData, time.Now().Add(webSocketWriteTimeout))
			}
			if err != nil {
				session.closeWith(websocket.CloseInternalServerErr, "write failed")
				return
			}
		}
	}
}

func (session *wsSession) writeClose() {
	code, reason := session.closeDetails()
	_ = session.connection.WriteControl(
		websocket.CloseMessage,
		websocket.FormatCloseMessage(code, reason),
		time.Now().Add(webSocketWriteTimeout),
	)
}

func (session *wsSession) handleCommand(command wsClientCommand) {
	switch command.Type {
	case wsCommandRunSubscribe:
		session.handleSubscribe(command)
	case wsCommandRunUnsubscribe:
		session.handleUnsubscribe(command)
	case wsCommandRunCancel:
		session.handleCancel(command)
	case wsCommandPing:
		payload := any(map[string]any{})
		if len(command.PingPayload) > 0 && string(command.PingPayload) != "null" {
			payload = command.PingPayload
		}
		session.sendEnvelope(wsTypePong, command.RequestID, payload)
	}
}

func (session *wsSession) handleSubscribe(command wsClientCommand) {
	session.subscriptionsMu.Lock()
	_, replacing := session.subscriptions[command.RunID]
	atLimit := !replacing && len(session.subscriptions) >= webSocketMaxSubscriptions
	session.subscriptionsMu.Unlock()
	if atLimit {
		session.sendEnvelope(wsTypeCommandError, command.RequestID, wsCommandErrorPayload{
			Code:      "subscription_limit_exceeded",
			Message:   fmt.Sprintf("a connection may subscribe to at most %d runs", webSocketMaxSubscriptions),
			Command:   command.Type,
			RunID:     command.RunID,
			Retryable: false,
		})
		return
	}
	source, err := session.service.SubscribeTask(session.ctx, session.ownerID, command.RunID, command.After)
	if err != nil {
		session.sendServiceError(command, err)
		return
	}
	snapshot, err := session.service.GetTask(session.ctx, session.ownerID, command.RunID)
	if err != nil {
		if source.Close != nil {
			source.Close()
		}
		session.sendServiceError(command, err)
		return
	}
	subscriptionContext, cancel := context.WithCancel(session.ctx)
	subscription := &wsRunSubscription{
		ctx:       subscriptionContext,
		cancel:    cancel,
		source:    source,
		requestID: command.RequestID,
		runID:     command.RunID,
		after:     command.After,
	}

	session.subscriptionsMu.Lock()
	previous := session.subscriptions[command.RunID]
	session.subscriptions[command.RunID] = subscription
	session.subscriptionsMu.Unlock()
	previous.stop()

	session.sendEnvelope(wsTypeCommandAck, command.RequestID, wsCommandAckPayload{Command: command.Type, RunID: command.RunID})
	session.sendEnvelope(wsTypeRunSnapshot, command.RequestID, snapshot)
	go session.forwardSubscription(subscription)
}

func (session *wsSession) handleUnsubscribe(command wsClientCommand) {
	session.subscriptionsMu.Lock()
	subscription := session.subscriptions[command.RunID]
	delete(session.subscriptions, command.RunID)
	session.subscriptionsMu.Unlock()
	subscription.stop()
	session.sendEnvelope(wsTypeCommandAck, command.RequestID, wsCommandAckPayload{Command: command.Type, RunID: command.RunID})
}

func (session *wsSession) handleCancel(command wsClientCommand) {
	// Holding the subscription lock makes the acknowledgement deterministic:
	// a terminal cancellation event cannot be queued ahead of its command ack.
	session.subscriptionsMu.Lock()
	snapshot, err := session.service.CancelTask(session.ctx, session.ownerID, command.RunID)
	if err == nil {
		session.sendEnvelope(wsTypeCommandAck, command.RequestID, wsCommandAckPayload{Command: command.Type, RunID: command.RunID})
		session.sendEnvelope(wsTypeRunSnapshot, command.RequestID, snapshot)
	}
	session.subscriptionsMu.Unlock()
	if err != nil {
		session.sendServiceError(command, err)
	}
}

func (session *wsSession) forwardSubscription(subscription *wsRunSubscription) {
	defer subscription.stop()
	defer session.removeSubscriptionIfCurrent(subscription)
	cursor := subscription.after
	for _, event := range subscription.source.Replay {
		if event.Sequence <= cursor {
			continue
		}
		if !session.sendSubscriptionEvent(subscription, event) {
			return
		}
		cursor = event.Sequence
		if event.Terminal() {
			return
		}
	}

	for {
		select {
		case <-subscription.ctx.Done():
			return
		case event, open := <-subscription.source.Events:
			if !open {
				if subscription.ctx.Err() != nil {
					return
				}
				// A terminal source can close normally when the caller resumes at
				// its latest cursor. For a running run, a closed source means the
				// in-memory subscriber overflowed or was otherwise interrupted;
				// force a resumable reconnect instead of silently losing events.
				snapshot, err := session.service.GetTask(session.ctx, session.ownerID, subscription.runID)
				if err == nil && snapshot.Status.Terminal() && cursor >= snapshot.LastEventSequence {
					// handleSubscribe already sent the authoritative snapshot. If the
					// caller's cursor is at the terminal watermark, there is nothing
					// left to replay and this subscription can end normally.
					return
				}
				session.closeWith(websocket.CloseTryAgainLater, "run stream interrupted; reconnect and resume with after")
				return
			}
			if event.Sequence <= cursor {
				continue
			}
			if !session.sendSubscriptionEvent(subscription, event) {
				return
			}
			cursor = event.Sequence
			if event.Terminal() {
				return
			}
		}
	}
}

func (session *wsSession) sendSubscriptionEvent(subscription *wsRunSubscription, event AgentEvent) bool {
	session.subscriptionsMu.Lock()
	defer session.subscriptionsMu.Unlock()
	if session.subscriptions[subscription.runID] != subscription {
		return false
	}
	return session.sendEnvelope(wsTypeRunEvent, subscription.requestID, event)
}

func (session *wsSession) removeSubscriptionIfCurrent(subscription *wsRunSubscription) {
	session.subscriptionsMu.Lock()
	defer session.subscriptionsMu.Unlock()
	if session.subscriptions[subscription.runID] == subscription {
		delete(session.subscriptions, subscription.runID)
	}
}

func (session *wsSession) stopAllSubscriptions() {
	session.subscriptionsMu.Lock()
	subscriptions := make([]*wsRunSubscription, 0, len(session.subscriptions))
	for runID, subscription := range session.subscriptions {
		delete(session.subscriptions, runID)
		subscriptions = append(subscriptions, subscription)
	}
	session.subscriptionsMu.Unlock()
	for _, subscription := range subscriptions {
		subscription.stop()
	}
}

func (session *wsSession) sendServiceError(command wsClientCommand, err error) {
	code := "internal_error"
	message := "command failed"
	retryable := false
	var lastEventSequence *uint64
	switch {
	case errors.Is(err, ErrTaskNotFound):
		code, message = "run_not_found", ErrTaskNotFound.Error()
	case errors.Is(err, ErrEventCursorAhead):
		code, message, retryable = "event_cursor_ahead", ErrEventCursorAhead.Error(), true
		if snapshot, snapshotErr := session.service.GetTask(session.ctx, session.ownerID, command.RunID); snapshotErr == nil {
			watermark := snapshot.LastEventSequence
			lastEventSequence = &watermark
		}
	case errors.Is(err, ErrTaskAlreadyFinished):
		code, message = "run_already_finished", ErrTaskAlreadyFinished.Error()
	case errors.Is(err, ErrTaskNotReady):
		code, message, retryable = "run_not_ready", ErrTaskNotReady.Error(), true
	case errors.Is(err, ErrInvalidRequest):
		code, message = "invalid_command", ErrInvalidRequest.Error()
	default:
		retryable = true
	}
	session.sendEnvelope(wsTypeCommandError, command.RequestID, wsCommandErrorPayload{
		Code: code, Message: message, Command: command.Type, RunID: command.RunID, Retryable: retryable, LastEventSequence: lastEventSequence,
	})
}

func (session *wsSession) sendCommandError(protocolErr *wsProtocolError) {
	if protocolErr == nil {
		return
	}
	session.sendEnvelope(wsTypeCommandError, protocolErr.RequestID, wsCommandErrorPayload{
		Code: protocolErr.Code, Message: protocolErr.Message, Command: protocolErr.Command, RunID: protocolErr.RunID,
	})
}

func (session *wsSession) reserveRequestID(requestID string) bool {
	if session.requestIDs == nil {
		session.requestIDs = make(map[string]struct{})
	}
	if _, exists := session.requestIDs[requestID]; exists {
		return false
	}
	if len(session.requestIDOrder) < webSocketRequestIDWindow {
		session.requestIDOrder = append(session.requestIDOrder, requestID)
	} else {
		evicted := session.requestIDOrder[session.requestIDCursor]
		delete(session.requestIDs, evicted)
		session.requestIDOrder[session.requestIDCursor] = requestID
		session.requestIDCursor = (session.requestIDCursor + 1) % webSocketRequestIDWindow
	}
	session.requestIDs[requestID] = struct{}{}
	return true
}

func duplicateRequestIDError(requestID, command string) *wsProtocolError {
	return &wsProtocolError{
		RequestID: requestID,
		Command:   command,
		Code:      "duplicate_request_id",
		Message:   "request_id must be unique within the connection",
	}
}

func (session *wsSession) sendEnvelope(messageType, requestID string, payload any) bool {
	return session.enqueue(wsOutbound{envelope: &wsServerEnvelope{
		SchemaVersion: CurrentSchemaVersion,
		Type:          messageType,
		RequestID:     requestID,
		CreatedAt:     time.Now().UTC(),
		Payload:       payload,
	}})
}

func (session *wsSession) enqueue(outbound wsOutbound) bool {
	if session.ctx.Err() != nil {
		return false
	}
	select {
	case session.send <- outbound:
		return true
	default:
		session.closeWith(websocket.CloseTryAgainLater, "client is too slow; reconnect and resume with after")
		return false
	}
}

func (session *wsSession) closeWith(code int, reason string) {
	session.closeMu.Lock()
	if !session.closeSet {
		session.closeSet = true
		session.closeCode = code
		session.closeReason = reason
	}
	session.closeMu.Unlock()
	session.cancel()
}

func (session *wsSession) closeDetails() (int, string) {
	session.closeMu.Lock()
	defer session.closeMu.Unlock()
	if !session.closeSet {
		return websocket.CloseNormalClosure, ""
	}
	return session.closeCode, session.closeReason
}

func decodeWSClientCommand(data []byte) (wsClientCommand, *wsProtocolError) {
	var envelope wsClientEnvelope
	if err := decodeStrictJSON(data, &envelope); err != nil {
		return wsClientCommand{}, &wsProtocolError{Code: "invalid_message", Message: "frame must be a valid protocol envelope"}
	}
	requestID := strings.TrimSpace(envelope.RequestID)
	commandType := strings.TrimSpace(envelope.Type)
	if requestID == "" || len(requestID) > 128 {
		return wsClientCommand{}, &wsProtocolError{RequestID: requestID, Command: commandType, Code: "invalid_command", Message: "request_id is required and must not exceed 128 characters"}
	}
	if envelope.SchemaVersion != CurrentSchemaVersion {
		return wsClientCommand{}, &wsProtocolError{RequestID: requestID, Command: commandType, Code: "unsupported_schema_version", Message: "schema_version must be " + CurrentSchemaVersion}
	}
	command := wsClientCommand{Type: commandType, RequestID: requestID}
	switch commandType {
	case wsCommandPing:
		if len(envelope.Payload) > 0 {
			command.PingPayload = append(json.RawMessage(nil), envelope.Payload...)
		}
		return command, nil
	case wsCommandRunSubscribe, wsCommandRunUnsubscribe, wsCommandRunCancel:
		var payload wsRunCommandPayload
		if err := decodeStrictJSON(envelope.Payload, &payload); err != nil {
			return wsClientCommand{}, &wsProtocolError{RequestID: requestID, Command: commandType, Code: "invalid_command", Message: "payload must contain a valid run_id"}
		}
		payload.RunID = normalizeTaskID(payload.RunID)
		if payload.RunID == "" || len(payload.RunID) > 128 {
			return wsClientCommand{}, &wsProtocolError{RequestID: requestID, Command: commandType, Code: "invalid_command", Message: "payload.run_id is required"}
		}
		if commandType != wsCommandRunSubscribe && payload.After != 0 {
			return wsClientCommand{}, &wsProtocolError{RequestID: requestID, Command: commandType, RunID: payload.RunID, Code: "invalid_command", Message: "payload.after is only valid for run.subscribe"}
		}
		command.RunID = payload.RunID
		command.After = payload.After
		return command, nil
	default:
		return wsClientCommand{}, &wsProtocolError{RequestID: requestID, Command: commandType, Code: "unsupported_command", Message: fmt.Sprintf("unsupported command %q", commandType)}
	}
}

func decodeStrictJSON(data []byte, target any) error {
	if len(bytes.TrimSpace(data)) == 0 {
		return io.ErrUnexpectedEOF
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return err
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		if err == nil {
			return errors.New("multiple JSON values are not allowed")
		}
		return err
	}
	return nil
}

func validWebSocketCloseCode(code int) bool {
	return code == websocket.CloseNormalClosure || code == websocket.CloseGoingAway ||
		code == websocket.CloseProtocolError || code == websocket.CloseUnsupportedData ||
		code == websocket.ClosePolicyViolation || code == websocket.CloseMessageTooBig ||
		code == websocket.CloseInternalServerErr || code == websocket.CloseServiceRestart ||
		code == websocket.CloseTryAgainLater || (code >= 3000 && code <= 4999)
}
