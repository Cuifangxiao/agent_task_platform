package middleware

import (
	"log/slog"
	"time"

	"agent-task-platform/apps/api/internal/transport/http/httpctx"

	"github.com/gin-gonic/gin"
)

func RequestLogger(logger *slog.Logger) gin.HandlerFunc {
	if logger == nil {
		logger = slog.Default()
	}

	return func(c *gin.Context) {
		start := time.Now()
		c.Next()

		latency := time.Since(start)
		status := c.Writer.Status()
		path := c.Request.URL.Path
		if rawQuery := c.Request.URL.RawQuery; rawQuery != "" {
			path += "?" + rawQuery
		}

		attrs := []any{
			"request_id", httpctx.RequestID(c),
			"method", c.Request.Method,
			"path", path,
			"status", status,
			"latency", latency.String(),
			"client_ip", c.ClientIP(),
			"user_agent", c.Request.UserAgent(),
		}
		if len(c.Errors) > 0 {
			attrs = append(attrs, "errors", c.Errors.String())
		}

		switch {
		case status >= 500:
			logger.Error("http request completed", attrs...)
		case status >= 400:
			logger.Warn("http request completed", attrs...)
		default:
			logger.Info("http request completed", attrs...)
		}
	}
}
