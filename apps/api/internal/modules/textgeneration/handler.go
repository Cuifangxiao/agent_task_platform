package textgeneration

import (
	"context"
	"errors"
	"io"
	"net/http"

	"agent-task-platform/apps/api/internal/shared/actor"
	"agent-task-platform/apps/api/internal/transport/http/httpctx"
	"agent-task-platform/apps/api/internal/transport/http/response"

	"github.com/gin-gonic/gin"
)

type Handler struct {
	service service
}

type service interface {
	StreamText(context.Context, actor.User, StreamTextRequest) (TextStream, error)
}

func NewHandler(service service) *Handler {
	return &Handler{service: service}
}

func (h *Handler) StreamText(c *gin.Context) {
	currentUser, ok := httpctx.Actor(c)
	if !ok {
		response.Internal(c, "missing auth claims")
		return
	}

	var req StreamTextRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		response.BadRequest(c, "invalid request", err)
		return
	}

	stream, err := h.service.StreamText(c.Request.Context(), currentUser, req)
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
