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

	"agent-task-platform/apps/api/internal/shared/actor"
	"agent-task-platform/apps/api/internal/transport/http/httpctx"
	"agent-task-platform/apps/api/internal/transport/http/response"

	"github.com/gin-gonic/gin"
)

const maxSourceFileBytes = 20 << 20
const maxTotalSourceFileBytes = 100 << 20
const maxGenerateDeckMultipartBytes = 101 << 20

var errUnsupportedSourceFile = errors.New("unsupported source file")
var errSourceFileTooLarge = errors.New("source file too large")

type Handler struct {
	service service
}

type service interface {
	GenerateDeck(context.Context, actor.User, GenerateDeckRequest) (DeckStream, error)
	ResumeDeck(context.Context, actor.User, ResumeDeckRequest) (DeckStream, error)
	CreateJob(context.Context, actor.User, CreateDeckJobRequest) (*DeckJobResponse, error)
	GetJob(context.Context, int64, string) (*DeckJobResponse, error)
	SubscribeJob(context.Context, int64, string, int64) (DeckStream, error)
	SubmitJobAction(context.Context, actor.User, string, DeckJobActionRequest) (*DeckJobResponse, bool, error)
	CancelJob(context.Context, int64, string) (*DeckJobResponse, error)
	ProductMetrics(context.Context, int64) (*DeckProductMetrics, error)
	RecordJobSubscriptionResume(context.Context, int64, string, int64)
	RecordJobSubscriptionDisconnect(context.Context, int64, string)
	RecordArtifactDownload(context.Context, int64, string)
	ListConversations(context.Context, int64) ([]ConversationSummaryResponse, error)
	GetConversation(context.Context, int64, int64) (*ConversationDetailResponse, error)
	DeleteConversation(context.Context, int64, int64) error
	ArtifactAssetPathForUser(context.Context, int64, string, string) (string, error)
}

func NewHandler(service service) *Handler {
	return &Handler{service: service}
}

func (h *Handler) GenerateDeck(c *gin.Context) {
	currentUser, ok := httpctx.Actor(c)
	if !ok {
		response.Internal(c, "missing auth claims")
		return
	}

	req, err := h.bindGenerateDeckRequest(c)
	if err != nil {
		respondGenerateRequestError(c, err)
		return
	}

	stream, err := h.service.GenerateDeck(c.Request.Context(), currentUser, req)
	if err != nil {
		h.respondError(c, err)
		return
	}
	defer stream.Close()

	h.writeDeckStream(c, stream)
}

func (h *Handler) CreateJob(c *gin.Context) {
	currentUser, ok := httpctx.Actor(c)
	if !ok {
		response.Internal(c, "missing auth claims")
		return
	}
	req, err := h.bindGenerateDeckRequest(c)
	if err != nil {
		respondGenerateRequestError(c, err)
		return
	}
	job, err := h.service.CreateJob(c.Request.Context(), currentUser, CreateDeckJobRequest{
		AgentConfigID: req.AgentConfigID,
		Description:   req.Description,
		Mode:          req.Mode,
		SourceFiles:   normalizeSourceFiles(req),
	})
	if err != nil {
		h.respondError(c, err)
		return
	}
	response.Accepted(c, job)
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
	currentUser, ok := httpctx.Actor(c)
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
	job, duplicate, err := h.service.SubmitJobAction(c.Request.Context(), currentUser, c.Param("job_id"), DeckJobActionRequest{
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
	currentUser, ok := httpctx.Actor(c)
	if !ok {
		response.Internal(c, "missing auth claims")
		return
	}

	var req ResumeDeckRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		response.BadRequest(c, "invalid request", err)
		return
	}

	stream, err := h.service.ResumeDeck(c.Request.Context(), currentUser, req)
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

func (h *Handler) DeleteConversation(c *gin.Context) {
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

	if err := h.service.DeleteConversation(c.Request.Context(), claims.UserID, req.ID); err != nil {
		h.respondError(c, err)
		return
	}
	response.OK(c, gin.H{"deleted": true, "id": req.ID})
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
			chunk.ArtifactURL = "/api/v1/presentations/artifacts/" + url.PathEscape(chunk.RequestID)
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
	var (
		req GenerateDeckRequest
		err error
	)
	if strings.HasPrefix(contentType, "multipart/form-data") {
		req, err = bindGenerateDeckMultipart(c)
	} else {
		err = c.ShouldBindJSON(&req)
	}
	if err != nil {
		return GenerateDeckRequest{}, err
	}
	if err := validateGenerateDeckRequest(req); err != nil {
		return GenerateDeckRequest{}, err
	}
	return req, nil
}

func bindGenerateDeckMultipart(c *gin.Context) (GenerateDeckRequest, error) {
	c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, maxGenerateDeckMultipartBytes)
	if err := c.Request.ParseMultipartForm(maxGenerateDeckMultipartBytes); err != nil {
		return GenerateDeckRequest{}, err
	}

	agentConfigID, err := parseOptionalInt64Form(c, "agent_config_id")
	if err != nil {
		return GenerateDeckRequest{}, err
	}
	req := GenerateDeckRequest{
		Description:   c.PostForm("description"),
		AgentConfigID: agentConfigID,
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

func parseOptionalInt64Form(c *gin.Context, key string) (int64, error) {
	raw := strings.TrimSpace(c.PostForm(key))
	if raw == "" {
		return 0, nil
	}
	value, err := strconv.ParseInt(raw, 10, 64)
	if err != nil || value <= 0 {
		return 0, fmt.Errorf("%s must be a positive integer", key)
	}
	return value, nil
}

func validateGenerateDeckRequest(req GenerateDeckRequest) error {
	if req.AgentConfigID < 0 {
		return fmt.Errorf("agent_config_id must be a positive integer")
	}
	mode := strings.TrimSpace(req.Mode)
	if mode != "" && mode != "quick" && mode != "step_by_step" {
		return fmt.Errorf("mode must be quick or step_by_step")
	}

	totalBytes := 0
	for _, sourceFile := range req.SourceFiles {
		if !supportedSourceFileName(sourceFile.FileName) {
			return fmt.Errorf("%w: %s", errUnsupportedSourceFile, sourceFile.FileName)
		}
		if len(sourceFile.Content) == 0 {
			return fmt.Errorf("source file is empty: %s", sourceFile.FileName)
		}
		if len(sourceFile.Content) > maxSourceFileBytes {
			return fmt.Errorf("%w: source_file must be <= %d bytes", errSourceFileTooLarge, maxSourceFileBytes)
		}
		totalBytes += len(sourceFile.Content)
		if totalBytes > maxTotalSourceFileBytes {
			return fmt.Errorf("%w: source files must total <= %d bytes", errSourceFileTooLarge, maxTotalSourceFileBytes)
		}
	}
	return nil
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
				return nil, fmt.Errorf("%w: %s", errUnsupportedSourceFile, header.Filename)
			}
			sourceFile, err := readSourceFileHeader(header)
			if err != nil {
				return nil, err
			}
			sourceFiles = append(sourceFiles, sourceFile)
			totalBytes += len(sourceFile.Content)
			if totalBytes > maxTotalSourceFileBytes {
				return nil, fmt.Errorf("%w: source files must total <= %d bytes", errSourceFileTooLarge, maxTotalSourceFileBytes)
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
		return nil, fmt.Errorf("%w: source_file must be <= %d bytes", errSourceFileTooLarge, maxSourceFileBytes)
	}
	data, err := io.ReadAll(io.LimitReader(file, maxSourceFileBytes+1))
	if err != nil {
		return nil, err
	}
	if len(data) > maxSourceFileBytes {
		return nil, fmt.Errorf("%w: source_file must be <= %d bytes", errSourceFileTooLarge, maxSourceFileBytes)
	}
	return data, nil
}

func respondGenerateRequestError(c *gin.Context, err error) {
	var maxBytesError *http.MaxBytesError
	switch {
	case errors.Is(err, errSourceFileTooLarge), errors.As(err, &maxBytesError):
		response.PayloadTooLarge(c, err.Error())
	case errors.Is(err, errUnsupportedSourceFile):
		response.UnsupportedMediaType(c, err.Error())
	default:
		response.BadRequest(c, "invalid request", err)
	}
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
	case errors.Is(err, ErrConversationActive):
		response.Conflict(c, err.Error())
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
