package pptagent

import (
	"errors"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"net/url"
	"strconv"
	"strings"

	"agent-task-platform/internal/httpctx"
	"agent-task-platform/internal/providercredential"
	"agent-task-platform/pkg/response"

	"github.com/gin-gonic/gin"
)

const maxSourcePDFBytes = 20 << 20
const maxGenerateDeckMultipartBytes = maxSourcePDFBytes + (2 << 20)

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

	req, err := h.bindGenerateDeckRequest(c)
	if err != nil {
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

func (h *Handler) bindGenerateDeckRequest(c *gin.Context) (GenerateDeckRequest, error) {
	contentType := strings.ToLower(c.GetHeader("Content-Type"))
	if strings.HasPrefix(contentType, "multipart/form-data") {
		return bindGenerateDeckMultipart(c)
	}

	var req GenerateDeckRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		return GenerateDeckRequest{}, err
	}
	return req, nil
}

func bindGenerateDeckMultipart(c *gin.Context) (GenerateDeckRequest, error) {
	c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, maxGenerateDeckMultipartBytes)
	if err := c.Request.ParseMultipartForm(maxGenerateDeckMultipartBytes); err != nil {
		return GenerateDeckRequest{}, err
	}

	agentConfigID, err := parseRequiredInt64Form(c, "agent_config_id")
	if err != nil {
		return GenerateDeckRequest{}, err
	}
	pageCount, err := parseOptionalIntForm(c, "page_count")
	if err != nil {
		return GenerateDeckRequest{}, err
	}

	req := GenerateDeckRequest{
		AgentConfigID: agentConfigID,
		Description:   c.PostForm("description"),
		Audience:      c.PostForm("audience"),
		Goal:          c.PostForm("goal"),
		PageCount:     pageCount,
		Language:      c.PostForm("language"),
		Style:         c.PostForm("style"),
		SourceText:    c.PostForm("source_text"),
	}

	file, header, err := sourcePDFFormFile(c)
	if err != nil {
		if !errors.Is(err, http.ErrMissingFile) {
			return GenerateDeckRequest{}, err
		}
		return req, nil
	}
	defer file.Close()

	data, err := readSourcePDF(file, header)
	if err != nil {
		return GenerateDeckRequest{}, err
	}
	req.SourcePDFName = header.Filename
	req.SourcePDFBytes = data
	return req, nil
}

func parseRequiredInt64Form(c *gin.Context, key string) (int64, error) {
	raw := strings.TrimSpace(c.PostForm(key))
	if raw == "" {
		return 0, fmt.Errorf("%s is required", key)
	}
	value, err := strconv.ParseInt(raw, 10, 64)
	if err != nil || value <= 0 {
		return 0, fmt.Errorf("%s must be a positive integer", key)
	}
	return value, nil
}

func parseOptionalIntForm(c *gin.Context, key string) (int, error) {
	raw := strings.TrimSpace(c.PostForm(key))
	if raw == "" {
		return 0, nil
	}
	value, err := strconv.Atoi(raw)
	if err != nil || value <= 0 {
		return 0, fmt.Errorf("%s must be a positive integer", key)
	}
	return value, nil
}

func sourcePDFFormFile(c *gin.Context) (multipart.File, *multipart.FileHeader, error) {
	file, header, err := c.Request.FormFile("source_pdf")
	if err == nil || !errors.Is(err, http.ErrMissingFile) {
		return file, header, err
	}
	return c.Request.FormFile("file")
}

func readSourcePDF(file multipart.File, header *multipart.FileHeader) ([]byte, error) {
	if header == nil {
		return nil, http.ErrMissingFile
	}
	if header.Size > maxSourcePDFBytes {
		return nil, fmt.Errorf("source_pdf must be <= %d bytes", maxSourcePDFBytes)
	}
	data, err := io.ReadAll(io.LimitReader(file, maxSourcePDFBytes+1))
	if err != nil {
		return nil, err
	}
	if len(data) > maxSourcePDFBytes {
		return nil, fmt.Errorf("source_pdf must be <= %d bytes", maxSourcePDFBytes)
	}
	return data, nil
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
	case errors.Is(err, ErrAgentConfigNotFound):
		response.NotFound(c, err.Error())
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
