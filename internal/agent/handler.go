package agent

import (
	"errors"
	"io"
	"net/http"

	"agent-task-platform/internal/httpctx"
	"agent-task-platform/internal/providercredential"
	"agent-task-platform/pkg/response"

	"github.com/gin-gonic/gin"
)

type Handler struct {
	service *Service
}

func NewHandler(service *Service) *Handler {
	return &Handler{service: service}
}

func (h *Handler) StreamText(c *gin.Context) {
	claims, ok := httpctx.Claims(c)
	if !ok {
		response.Internal(c, "missing auth claims")
		return
	}

	var req StreamTextRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		response.BadRequest(c, "invalid request", err)
		return
	}

	stream, err := h.service.StreamText(c.Request.Context(), providercredential.UserContext{
		UserID:    claims.UserID,
		Username:  claims.Username,
		SessionID: claims.SessionID,
		Device:    claims.Device,
	}, req)
	if err != nil {
		h.respondError(c, err)
		return
	}
	defer stream.Close()

	c.Header("Content-Type", "text/event-stream")
	c.Header("Cache-Control", "no-cache")
	c.Header("Connection", "keep-alive")
	c.Header("X-Accel-Buffering", "no")
	c.Status(http.StatusOK)
	c.Writer.Flush()

	doneSent := false
	for {
		chunk, err := stream.Recv()
		if err != nil {
			if errors.Is(err, io.EOF) {
				if !doneSent {
					c.SSEvent("done", TextStreamChunk{Done: true})
					c.Writer.Flush()
				}
				return
			}

			c.SSEvent("error", TextStreamChunk{
				Done:  true,
				Error: "stream interrupted",
			})
			c.Writer.Flush()
			return
		}
		if chunk == nil {
			continue
		}

		if chunk.Error != "" {
			c.SSEvent("error", chunk)
			c.Writer.Flush()
			doneSent = chunk.Done
			if chunk.Done {
				return
			}
			continue
		}

		if chunk.Delta != "" {
			c.SSEvent("delta", chunk)
			c.Writer.Flush()
		}
		if chunk.Done {
			c.SSEvent("done", chunk)
			c.Writer.Flush()
			doneSent = true
			return
		}
	}
}

func (h *Handler) respondError(c *gin.Context, err error) {
	switch {
	case errors.Is(err, ErrInvalidRequest):
		response.BadRequest(c, err.Error(), nil)
	case errors.Is(err, ErrModelConfigNotFound):
		response.NotFound(c, err.Error())
	case errors.Is(err, ErrModelConfigInactive):
		response.BadRequest(c, err.Error(), nil)
	default:
		response.InternalServerError(c)
	}
}
