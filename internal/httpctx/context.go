package httpctx

import (
	"agent-task-platform/internal/auth"

	"github.com/gin-gonic/gin"
)

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
