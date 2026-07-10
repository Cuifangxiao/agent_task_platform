package middleware

import (
	"log/slog"
	"runtime/debug"

	"agent-task-platform/apps/api/internal/transport/http/response"
	"github.com/gin-gonic/gin"
)

func Recovery(logger *slog.Logger) gin.HandlerFunc {
	if logger == nil {
		logger = slog.Default()
	}

	return gin.CustomRecovery(func(c *gin.Context, recovered any) {
		logger.Error(
			"panic recovered",
			"method", c.Request.Method,
			"path", c.Request.URL.RequestURI(),
			"client_ip", c.ClientIP(),
			"panic", recovered,
			"stack", string(debug.Stack()),
		)

		response.InternalServerError(c)
		c.Abort()
	})
}
