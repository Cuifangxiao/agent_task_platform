package middleware

import (
	"errors"
	"strings"

	"agent-task-platform/apps/api/internal/transport/http/response"

	"github.com/gin-gonic/gin"
)

const AgentTaskWebSocketProtocol = "agent-task-v1"

const (
	webSocketBearerPrefix = "bearer."
	maxWebSocketTokenSize = 16 << 10
)

var errInvalidWebSocketAuthProtocol = errors.New("websocket authentication subprotocol is invalid")

// WebSocketSubprotocolAuth extracts the browser-compatible bearer subprotocol
// into the Authorization header consumed by the regular Auth middleware. It
// rewrites the requested protocol list before the upgrade so the JWT can never
// be selected or echoed in Sec-WebSocket-Protocol.
func WebSocketSubprotocolAuth() gin.HandlerFunc {
	return func(c *gin.Context) {
		rawProtocols := strings.Join(c.Request.Header.Values("Sec-WebSocket-Protocol"), ",")
		token, err := extractWebSocketBearer(rawProtocols)
		if err != nil {
			response.Unauthorized(c, "websocket authentication is required")
			c.Abort()
			return
		}

		c.Request.Header.Set("Authorization", "Bearer "+token)
		c.Request.Header.Set("Sec-WebSocket-Protocol", AgentTaskWebSocketProtocol)
		c.Next()
	}
}

func extractWebSocketBearer(rawProtocols string) (string, error) {
	foundApplicationProtocol := false
	token := ""
	for _, item := range strings.Split(rawProtocols, ",") {
		protocol := strings.TrimSpace(item)
		switch {
		case protocol == AgentTaskWebSocketProtocol:
			foundApplicationProtocol = true
		case strings.HasPrefix(protocol, webSocketBearerPrefix):
			if token != "" {
				return "", errInvalidWebSocketAuthProtocol
			}
			token = strings.TrimPrefix(protocol, webSocketBearerPrefix)
		}
	}
	if !foundApplicationProtocol || token == "" || len(token) > maxWebSocketTokenSize || strings.ContainsAny(token, " \t\r\n,") {
		return "", errInvalidWebSocketAuthProtocol
	}
	return token, nil
}
