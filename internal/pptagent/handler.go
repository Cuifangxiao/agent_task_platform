package pptagent

import (
	"errors"
	"io"
	"net/http"
	"net/url"

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

func (h *Handler) GenerateDeck(c *gin.Context) {
	claims, ok := httpctx.Claims(c)
	if !ok {
		response.Internal(c, "missing auth claims")
		return
	}

	var req GenerateDeckRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		response.BadRequest(c, "invalid request", err)
		return
	}

	stream, err := h.service.GenerateDeck(c.Request.Context(), providercredential.UserContext{
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
					c.SSEvent("done", DeckStreamChunk{Done: true})
					c.Writer.Flush()
				}
				return
			}

			c.SSEvent("error", DeckStreamChunk{
				EventType: "error",
				Done:      true,
				Error:     "stream interrupted",
			})
			c.Writer.Flush()
			return
		}
		if chunk == nil {
			continue
		}

		eventName := chunk.EventType
		if eventName == "" {
			eventName = "progress"
			chunk.EventType = eventName
		}
		if eventName == "artifact" && chunk.RequestID != "" {
			chunk.ArtifactURL = "/ppt-agent/artifacts/" + url.PathEscape(chunk.RequestID)
		}
		if chunk.Error != "" {
			eventName = "error"
			chunk.EventType = eventName
		}

		c.SSEvent(eventName, chunk)
		c.Writer.Flush()

		if chunk.Done {
			doneSent = true
			return
		}
	}
}

func (h *Handler) DownloadArtifact(c *gin.Context) {
	path, err := h.service.ArtifactPath(c.Param("request_id"))
	if err != nil {
		h.respondError(c, err)
		return
	}

	c.FileAttachment(path, "deck-"+c.Param("request_id")+".pptx")
}

func (h *Handler) respondError(c *gin.Context, err error) {
	switch {
	case errors.Is(err, ErrInvalidRequest):
		response.BadRequest(c, err.Error(), nil)
	case errors.Is(err, ErrModelConfigNotFound):
		response.NotFound(c, err.Error())
	case errors.Is(err, ErrModelConfigInactive):
		response.BadRequest(c, err.Error(), nil)
	case errors.Is(err, ErrArtifactNotFound):
		response.NotFound(c, err.Error())
	default:
		response.InternalServerError(c)
	}
}
