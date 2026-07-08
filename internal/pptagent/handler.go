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

const maxSourceFileBytes = 20 << 20
const maxGenerateDeckMultipartBytes = 256 << 20

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

	h.writeDeckStream(c, stream)
}

func (h *Handler) ResumeDeck(c *gin.Context) {
	claims, ok := httpctx.Claims(c)
	if !ok {
		response.Internal(c, "missing auth claims")
		return
	}

	var req ResumeDeckRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		response.BadRequest(c, "invalid request", err)
		return
	}

	stream, err := h.service.ResumeDeck(c.Request.Context(), providercredential.UserContext{
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

	h.writeDeckStream(c, stream)
}

func (h *Handler) ListConversations(c *gin.Context) {
	claims, ok := httpctx.Claims(c)
	if !ok {
		response.Internal(c, "missing auth claims")
		return
	}

	resp, err := h.service.ListConversations(c.Request.Context(), claims.UserID)
	if err != nil {
		h.respondError(c, err)
		return
	}

	response.OK(c, resp)
}

func (h *Handler) GetConversation(c *gin.Context) {
	claims, ok := httpctx.Claims(c)
	if !ok {
		response.Internal(c, "missing auth claims")
		return
	}

	var req struct {
		ID int64 `uri:"id" binding:"required,min=1"`
	}
	if err := c.ShouldBindUri(&req); err != nil {
		response.BadRequest(c, "invalid path", err)
		return
	}

	resp, err := h.service.GetConversation(c.Request.Context(), claims.UserID, req.ID)
	if err != nil {
		h.respondError(c, err)
		return
	}

	response.OK(c, resp)
}

func (h *Handler) writeDeckStream(c *gin.Context, stream DeckStream) {
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

	req := GenerateDeckRequest{
		Description:   c.PostForm("description"),
		AgentConfigID: parseOptionalInt64Form(c, "agent_config_id"),
	}

	sourceFiles, err := sourceFilesFromMultipart(c)
	if err != nil {
		return GenerateDeckRequest{}, err
	}
	req.SourceFiles = sourceFiles
	if len(sourceFiles) > 0 {
		req.SourceFileName = sourceFiles[0].FileName
		req.SourceFile = sourceFiles[0].Content
	}
	return req, nil
}

func parseOptionalInt64Form(c *gin.Context, key string) int64 {
	raw := strings.TrimSpace(c.PostForm(key))
	if raw == "" {
		return 0
	}
	value, err := strconv.ParseInt(raw, 10, 64)
	if err != nil || value <= 0 {
		return 0
	}
	return value
}

func sourceFilesFromMultipart(c *gin.Context) ([]SourceFile, error) {
	if c.Request.MultipartForm == nil {
		return nil, nil
	}

	var sourceFiles []SourceFile
	for _, key := range []string{"source_files", "source_file", "file"} {
		for _, header := range c.Request.MultipartForm.File[key] {
			sourceFile, err := readSourceFileHeader(header)
			if err != nil {
				return nil, err
			}
			sourceFiles = append(sourceFiles, sourceFile)
		}
	}
	return sourceFiles, nil
}

func readSourceFileHeader(header *multipart.FileHeader) (SourceFile, error) {
	if header == nil {
		return SourceFile{}, http.ErrMissingFile
	}
	file, err := header.Open()
	if err != nil {
		return SourceFile{}, err
	}
	defer file.Close()

	data, err := readSourceFile(file, header)
	if err != nil {
		return SourceFile{}, err
	}
	return SourceFile{
		FileName: header.Filename,
		Content:  data,
	}, nil
}

func readSourceFile(file multipart.File, header *multipart.FileHeader) ([]byte, error) {
	if header == nil {
		return nil, http.ErrMissingFile
	}
	if header.Size > maxSourceFileBytes {
		return nil, fmt.Errorf("source_file must be <= %d bytes", maxSourceFileBytes)
	}
	data, err := io.ReadAll(io.LimitReader(file, maxSourceFileBytes+1))
	if err != nil {
		return nil, err
	}
	if len(data) > maxSourceFileBytes {
		return nil, fmt.Errorf("source_file must be <= %d bytes", maxSourceFileBytes)
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
	case errors.Is(err, ErrConversationNotFound):
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
