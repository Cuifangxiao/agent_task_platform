package httpctx

import (
	"agent-task-platform/apps/api/internal/modules/auth"
	"agent-task-platform/apps/api/internal/shared/actor"

	"github.com/gin-gonic/gin"
)

const requestIDKey = "http.request_id"

func Claims(c *gin.Context) (*auth.Claims, bool) {
	claimsValue, exists := c.Get(auth.ContextKeyClaims)
	if !exists {
		return nil, false
	}

	claims, ok := claimsValue.(*auth.Claims)
	if !ok || claims == nil {
		return nil, false
	}

	return claims, true
}

// Actor returns the transport-independent authenticated caller.
func Actor(c *gin.Context) (actor.User, bool) {
	claims, ok := Claims(c)
	if !ok {
		return actor.User{}, false
	}

	return actor.User{
		UserID:    claims.UserID,
		Username:  claims.Username,
		SessionID: claims.SessionID,
		Device:    claims.Device,
	}, true
}

func SetRequestID(c *gin.Context, requestID string) {
	c.Set(requestIDKey, requestID)
}

func RequestID(c *gin.Context) string {
	requestID, _ := c.Get(requestIDKey)
	value, _ := requestID.(string)
	return value
}
