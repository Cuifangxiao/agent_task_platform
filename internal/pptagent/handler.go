package pptagent

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"net/url"
	"path/filepath"
	"strconv"
	"strings"

	"agent-task-platform/internal/httpctx"
	"agent-task-platform/internal/providercredential"
	"agent-task-platform/pkg/response"

	"github.com/gin-gonic/gin"
)

const maxSourceFileBytes = 20 << 20
const maxTotalSourceFileBytes = 100 << 20
const maxGenerateDeckMultipartBytes = 101 << 20

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

func (h *Handler) CreateJob(c *gin.Context) {
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
	job, err := h.service.CreateJob(c.Request.Context(), providercredential.UserContext{
		UserID: claims.UserID, Username: claims.Username, SessionID: claims.SessionID, Device: claims.Device,
	}, CreateDeckJobRequest{AgentConfigID: req.AgentConfigID, Description: req.Description, Mode: req.Mode, SourceFiles: normalizeSourceFiles(req)})
	if err != nil {
		h.respondError(c, err)
		return
	}
	c.JSON(http.StatusAccepted, job)
}

func (h *Handler) GetJob(c *gin.Context) {
	claims, ok := httpctx.Claims(c)
	if !ok {
		response.Internal(c, "missing auth claims")
		return
	}
	job, err := h.service.GetJob(c.Request.Context(), claims.UserID, c.Param("job_id"))
	if err != nil {
		h.respondError(c, err)
		return
	}
	response.OK(c, job)
}

func (h *Handler) SubscribeJob(c *gin.Context) {
	claims, ok := httpctx.Claims(c)
	if !ok {
		response.Internal(c, "missing auth claims")
		return
	}
	after := parseEventSequence(c.GetHeader("Last-Event-ID"))
	if queryAfter := parseEventSequence(c.Query("after")); queryAfter > after {
		after = queryAfter
	}
	h.service.RecordJobSubscriptionResume(context.Background(), claims.UserID, c.Param("job_id"), after)
	stream, err := h.service.SubscribeJob(c.Request.Context(), claims.UserID, c.Param("job_id"), after)
	if err != nil {
		h.respondError(c, err)
		return
	}
	defer stream.Close()
	h.writeDeckStream(c, stream)
	if c.Request.Context().Err() != nil {
		h.service.RecordJobSubscriptionDisconnect(context.Background(), claims.UserID, c.Param("job_id"))
	}
}

func (h *Handler) ProductMetrics(c *gin.Context) {
	claims, ok := httpctx.Claims(c)
	if !ok {
		response.Internal(c, "missing auth claims")
		return
	}
	metrics, err := h.service.ProductMetrics(c.Request.Context(), claims.UserID)
	if err != nil {
		h.respondError(c, err)
		return
	}
	response.OK(c, metrics)
}

func (h *Handler) JobAction(c *gin.Context) {
	claims, ok := httpctx.Claims(c)
	if !ok {
		response.Internal(c, "missing auth claims")
		return
	}
	var body struct {
		Action          string          `json:"action" binding:"required"`
		OutlineMarkdown string          `json:"outline_markdown"`
		Payload         json.RawMessage `json:"payload"`
	}
	if err := c.ShouldBindJSON(&body); err != nil {
		response.BadRequest(c, "invalid request", err)
		return
	}
	job, duplicate, err := h.service.SubmitJobAction(c.Request.Context(), providercredential.UserContext{
		UserID: claims.UserID, Username: claims.Username, SessionID: claims.SessionID, Device: claims.Device,
	}, c.Param("job_id"), DeckJobActionRequest{
		Action: body.Action, OutlineMarkdown: body.OutlineMarkdown, Payload: body.Payload,
		IdempotencyKey: c.GetHeader("Idempotency-Key"),
	})
	if err != nil {
		h.respondError(c, err)
		return
	}
	response.OK(c, gin.H{"job": job, "duplicate": duplicate})
}

func (h *Handler) CancelJob(c *gin.Context) {
	claims, ok := httpctx.Claims(c)
	if !ok {
		response.Internal(c, "missing auth claims")
		return
	}
	job, err := h.service.CancelJob(c.Request.Context(), claims.UserID, c.Param("job_id"))
	if err != nil {
		h.respondError(c, err)
		return
	}
	response.OK(c, job)
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
			if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
				return
			}

			c.SSEvent("error", DeckStreamChunk{
				EventVersion: DeckEventVersion,
				EventType:    "error",
				StageStatus:  "error",
				Severity:     "error",
				Retryable:    true,
				ErrorCode:    "subscriber_stream_error",
				Done:         true,
				Error:        safeErrorSummary(err),
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

		data, marshalErr := json.Marshal(chunk)
		if marshalErr != nil {
			return
		}
		if chunk.Sequence > 0 {
			_, _ = fmt.Fprintf(c.Writer, "id: %d\n", chunk.Sequence)
		}
		_, _ = fmt.Fprintf(c.Writer, "event: %s\ndata: %s\n\n", eventName, data)
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
		Mode:          c.PostForm("mode"),
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
	totalBytes := 0
	for _, key := range []string{"source_files", "source_file", "file"} {
		for _, header := range c.Request.MultipartForm.File[key] {
			if !supportedSourceFileName(header.Filename) {
				return nil, fmt.Errorf("unsupported source file format: %s", header.Filename)
			}
			sourceFile, err := readSourceFileHeader(header)
			if err != nil {
				return nil, err
			}
			sourceFiles = append(sourceFiles, sourceFile)
			totalBytes += len(sourceFile.Content)
			if totalBytes > maxTotalSourceFileBytes {
				return nil, fmt.Errorf("source files must total <= %d bytes", maxTotalSourceFileBytes)
			}
		}
	}
	return sourceFiles, nil
}

func supportedSourceFileName(name string) bool {
	switch strings.ToLower(filepath.Ext(strings.TrimSpace(name))) {
	case ".pdf", ".txt", ".md", ".markdown", ".csv", ".json", ".yaml", ".yml", ".docx":
		return true
	default:
		return false
	}
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
	claims, ok := httpctx.Claims(c)
	if !ok {
		response.Internal(c, "missing auth claims")
		return
	}
	assetPath := strings.TrimPrefix(c.Param("asset_path"), "/")
	path, err := h.service.ArtifactAssetPathForUser(c.Request.Context(), claims.UserID, c.Param("request_id"), assetPath)
	if err != nil {
		h.respondError(c, err)
		return
	}
	h.service.RecordArtifactDownload(context.Background(), claims.UserID, c.Param("request_id"))

	if assetPath == "" || strings.HasSuffix(strings.ToLower(path), ".pptx") {
		c.FileAttachment(path, "deck-"+c.Param("request_id")+".pptx")
		return
	}
	if strings.HasSuffix(strings.ToLower(path), ".html") {
		c.Header("Content-Security-Policy", "default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:; sandbox")
		c.Header("X-Content-Type-Options", "nosniff")
	}
	c.File(path)
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
	case errors.Is(err, ErrDeckJobNotFound):
		response.NotFound(c, err.Error())
	default:
		response.InternalServerError(c)
	}
}

func parseEventSequence(value string) int64 {
	sequence, err := strconv.ParseInt(strings.TrimSpace(value), 10, 64)
	if err != nil || sequence < 0 {
		return 0
	}
	return sequence
}
