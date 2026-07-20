package middleware

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/gin-gonic/gin"
)

func TestExtractWebSocketBearer(t *testing.T) {
	token, err := extractWebSocketBearer(" agent-task-v1, bearer.header.payload.signature ")
	if err != nil {
		t.Fatal(err)
	}
	if token != "header.payload.signature" {
		t.Fatalf("token = %q", token)
	}

	invalid := []string{
		"",
		"agent-task-v1",
		"bearer.header.payload.signature",
		"agent-task-v1, bearer.",
		"agent-task-v1, bearer.one, bearer.two",
	}
	for _, protocols := range invalid {
		if _, err := extractWebSocketBearer(protocols); err == nil {
			t.Errorf("protocols %q were accepted", protocols)
		}
	}
}

func TestWebSocketSubprotocolAuthRewritesCredentialBeforeNextMiddleware(t *testing.T) {
	gin.SetMode(gin.TestMode)
	engine := gin.New()
	var authorization string
	var protocols string
	engine.GET("/ws", WebSocketSubprotocolAuth(), func(c *gin.Context) {
		authorization = c.GetHeader("Authorization")
		protocols = c.GetHeader("Sec-WebSocket-Protocol")
		c.Status(http.StatusNoContent)
	})

	request := httptest.NewRequest(http.MethodGet, "/ws", nil)
	request.Header.Set("Sec-WebSocket-Protocol", "agent-task-v1, bearer.secret.jwt.token")
	response := httptest.NewRecorder()
	engine.ServeHTTP(response, request)

	if response.Code != http.StatusNoContent {
		t.Fatalf("status = %d, body = %s", response.Code, response.Body.String())
	}
	if authorization != "Bearer secret.jwt.token" {
		t.Fatalf("authorization = %q", authorization)
	}
	if protocols != AgentTaskWebSocketProtocol {
		t.Fatalf("protocols = %q", protocols)
	}
	if got := response.Header().Get("Sec-WebSocket-Protocol"); got != "" {
		t.Fatalf("middleware unexpectedly echoed a subprotocol: %q", got)
	}
}

func TestWebSocketSubprotocolAuthRejectsMissingBearer(t *testing.T) {
	gin.SetMode(gin.TestMode)
	engine := gin.New()
	called := false
	engine.GET("/ws", WebSocketSubprotocolAuth(), func(c *gin.Context) { called = true })
	request := httptest.NewRequest(http.MethodGet, "/ws", nil)
	request.Header.Set("Sec-WebSocket-Protocol", AgentTaskWebSocketProtocol)
	response := httptest.NewRecorder()
	engine.ServeHTTP(response, request)

	if response.Code != http.StatusUnauthorized || called {
		t.Fatalf("status=%d called=%v body=%s", response.Code, called, response.Body.String())
	}
}
