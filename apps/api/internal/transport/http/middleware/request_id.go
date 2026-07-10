package middleware

import (
	"crypto/rand"
	"encoding/hex"
	"strings"

	"agent-task-platform/apps/api/internal/transport/http/httpctx"

	"github.com/gin-gonic/gin"
)

const requestIDHeader = "X-Request-ID"

// RequestID propagates a caller-provided request ID or creates one for logs
// and versioned API responses.
func RequestID() gin.HandlerFunc {
	return func(c *gin.Context) {
		requestID := strings.TrimSpace(c.GetHeader(requestIDHeader))
		if !validRequestID(requestID) {
			requestID = newRequestID()
		}

		httpctx.SetRequestID(c, requestID)
		c.Header(requestIDHeader, requestID)
		c.Next()
	}
}

func validRequestID(value string) bool {
	if value == "" || len(value) > 128 {
		return false
	}
	for _, char := range value {
		if (char >= 'a' && char <= 'z') ||
			(char >= 'A' && char <= 'Z') ||
			(char >= '0' && char <= '9') ||
			char == '-' || char == '_' || char == '.' {
			continue
		}
		return false
	}
	return true
}

func newRequestID() string {
	buffer := make([]byte, 16)
	if _, err := rand.Read(buffer); err != nil {
		return "request-unknown"
	}
	return hex.EncodeToString(buffer)
}
