package middleware

import (
	"strings"

	"agent-task-platform/apps/api/internal/modules/auth"
	"agent-task-platform/apps/api/internal/transport/http/response"
	"github.com/gin-gonic/gin"
)

func Auth(jwtManager *auth.Manager, sessionStore auth.SessionStore) gin.HandlerFunc {
	return func(c *gin.Context) {
		authHeader := c.GetHeader("Authorization")
		if authHeader == "" {
			response.Unauthorized(c, "authorization header is required")
			c.Abort()
			return
		}

		parts := strings.SplitN(authHeader, " ", 2)
		if len(parts) != 2 || !strings.EqualFold(parts[0], "Bearer") {
			response.Unauthorized(c, "authorization header format must be Bearer <token>")
			c.Abort()
			return
		}

		claims, err := jwtManager.ParseToken(parts[1])
		if err != nil {
			response.Unauthorized(c, "invalid or expired token")
			c.Abort()
			return
		}
		if err := claims.RequireTokenType(auth.TokenTypeAccess); err != nil {
			response.Unauthorized(c, "invalid or expired token")
			c.Abort()
			return
		}

		isBlacklisted, err := sessionStore.IsAccessTokenBlacklisted(c.Request.Context(), claims.ID)
		if err != nil {
			response.Internal(c, "failed to validate token")
			c.Abort()
			return
		}
		if isBlacklisted {
			response.Unauthorized(c, "invalid or expired token")
			c.Abort()
			return
		}

		session, err := sessionStore.GetSession(c.Request.Context(), claims.UserID, claims.Device)
		if err != nil {
			response.Internal(c, "failed to validate session")
			c.Abort()
			return
		}
		if session == nil || session.Status != auth.SessionStatusActive || session.SessionID != claims.SessionID {
			response.Unauthorized(c, "invalid or expired token")
			c.Abort()
			return
		}

		c.Set("userID", claims.UserID)
		c.Set("username", claims.Username)
		c.Set(auth.ContextKeyClaims, claims)
		c.Next()
	}
}
