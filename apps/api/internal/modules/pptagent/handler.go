package pptagent

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"mime"
	"mime/multipart"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"agent-task-platform/apps/api/internal/shared/actor"
	"agent-task-platform/apps/api/internal/transport/http/httpctx"
	"agent-task-platform/apps/api/internal/transport/http/response"

	"github.com/gin-gonic/gin"
)

const (
	maxAttachmentBytes      = 20 << 20
	maxTotalAttachmentBytes = 100 << 20
	maxMultipartBytes       = maxTotalAttachmentBytes + (1 << 20)
	maxJSONBodyBytes        = 64 << 10
)

var (
	errUnsupportedAttachment = errors.New("unsupported attachment")
	errAttachmentTooLarge    = errors.New("attachment too large")
)

type taskService interface {
	CreateTask(context.Context, actor.User, CreateTaskRequest) (*Task, error)
	GetTask(context.Context, int64, string) (*Task, error)
	ListTasks(context.Context, int64, string, int) ([]*Task, error)
	SubscribeTask(context.Context, int64, string, uint64) (*Subscription, error)
	CancelTask(context.Context, int64, string) (*Task, error)
	OpenArtifact(context.Context, int64, string, string) (*os.File, error)
}

type Handler struct {
	service taskService
}

func NewHandler(service taskService) *Handler {
	return &Handler{service: service}
}

func (h *Handler) CreateTask(c *gin.Context) {
	user, ok := httpctx.Actor(c)
	if !ok {
		response.Internal(c, "missing auth claims")
		return
	}
	req, err := bindCreateTask(c)
	if err != nil {
		respondCreateError(c, err)
		return
	}
	task, err := h.service.CreateTask(c.Request.Context(), user, req)
	if err != nil {
		h.respondError(c, err)
		return
	}
	response.Accepted(c, task)
}

func (h *Handler) GetTask(c *gin.Context) {
	claims, ok := httpctx.Claims(c)
	if !ok {
		response.Internal(c, "missing auth claims")
		return
	}
	task, err := h.service.GetTask(c.Request.Context(), claims.UserID, c.Param("task_id"))
	if err != nil {
		h.respondError(c, err)
		return
	}
	response.OK(c, task)
}

func (h *Handler) ListTasks(c *gin.Context) {
	claims, ok := httpctx.Claims(c)
	if !ok {
		response.Internal(c, "missing auth claims")
		return
	}
	limit, err := parseTaskListLimit(c.Query("limit"))
	if err != nil {
		response.BadRequest(c, "invalid request", err)
		return
	}
	tasks, err := h.service.ListTasks(c.Request.Context(), claims.UserID, c.Query("thread_id"), limit)
	if err != nil {
		h.respondError(c, err)
		return
	}
	response.OK(c, tasks)
}

func (h *Handler) CancelTask(c *gin.Context) {
	claims, ok := httpctx.Claims(c)
	if !ok {
		response.Internal(c, "missing auth claims")
		return
	}
	task, err := h.service.CancelTask(c.Request.Context(), claims.UserID, c.Param("task_id"))
	if err != nil {
		h.respondError(c, err)
		return
	}
	response.OK(c, task)
}

func (h *Handler) DownloadFile(c *gin.Context) {
	claims, ok := httpctx.Claims(c)
	if !ok {
		response.Internal(c, "missing auth claims")
		return
	}
	taskID := c.Param("task_id")
	file, err := h.service.OpenArtifact(c.Request.Context(), claims.UserID, taskID, "")
	if err != nil {
		h.respondError(c, err)
		return
	}
	defer file.Close()
	serveArtifact(c, file, "deck-"+taskID+".pptx", true)
}

func (h *Handler) DownloadArtifact(c *gin.Context) {
	claims, ok := httpctx.Claims(c)
	if !ok {
		response.Internal(c, "missing auth claims")
		return
	}
	relativePath := strings.TrimPrefix(c.Param("asset_path"), "/")
	file, err := h.service.OpenArtifact(c.Request.Context(), claims.UserID, c.Param("task_id"), relativePath)
	if err != nil {
		h.respondError(c, err)
		return
	}
	defer file.Close()
	extension := strings.ToLower(filepath.Ext(file.Name()))
	if extension == ".html" || extension == ".svg" {
		c.Header("Content-Security-Policy", "default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:; sandbox")
		c.Header("X-Content-Type-Options", "nosniff")
	}
	serveArtifact(c, file, filepath.Base(file.Name()), false)
}

func serveArtifact(c *gin.Context, file *os.File, downloadName string, attachment bool) {
	info, err := file.Stat()
	if err != nil {
		response.NotFound(c, ErrArtifactNotFound.Error())
		return
	}
	if attachment {
		c.Header("Content-Disposition", mime.FormatMediaType("attachment", map[string]string{"filename": downloadName}))
	}
	http.ServeContent(c.Writer, c.Request, downloadName, info.ModTime(), file)
}

func bindCreateTask(c *gin.Context) (CreateTaskRequest, error) {
	if strings.HasPrefix(strings.ToLower(c.GetHeader("Content-Type")), "multipart/form-data") {
		return bindMultipartTask(c)
	}
	c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, maxJSONBodyBytes)
	var body struct {
		ThreadID        string `json:"thread_id"`
		ClientRequestID string `json:"client_request_id"`
		AgentConfigID   int64  `json:"agent_config_id"`
		Description     string `json:"description"`
	}
	if err := c.ShouldBindJSON(&body); err != nil {
		return CreateTaskRequest{}, err
	}
	return CreateTaskRequest{
		ThreadID:        body.ThreadID,
		ClientRequestID: body.ClientRequestID,
		AgentConfigID:   body.AgentConfigID,
		Description:     body.Description,
	}, nil
}

func bindMultipartTask(c *gin.Context) (CreateTaskRequest, error) {
	c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, maxMultipartBytes)
	if err := c.Request.ParseMultipartForm(maxMultipartBytes); err != nil {
		return CreateTaskRequest{}, err
	}
	if c.Request.MultipartForm != nil {
		defer c.Request.MultipartForm.RemoveAll()
	}
	agentConfigID, err := parsePositiveInt64(c.PostForm("agent_config_id"))
	if err != nil {
		return CreateTaskRequest{}, fmt.Errorf("agent_config_id: %w", err)
	}
	attachments, err := readAttachments(c.Request.MultipartForm)
	if err != nil {
		return CreateTaskRequest{}, err
	}
	return CreateTaskRequest{
		ThreadID:        c.PostForm("thread_id"),
		ClientRequestID: c.PostForm("client_request_id"),
		AgentConfigID:   agentConfigID,
		Description:     c.PostForm("description"),
		Attachments:     attachments,
	}, nil
}

func readAttachments(form *multipart.Form) ([]InputResource, error) {
	if form == nil {
		return nil, nil
	}
	attachments := make([]InputResource, 0)
	total := 0
	for _, field := range []string{"attachments", "source_files", "source_file", "file"} {
		for _, header := range form.File[field] {
			attachment, err := readAttachment(header)
			if err != nil {
				return nil, err
			}
			total += len(attachment.Content)
			if total > maxTotalAttachmentBytes {
				return nil, fmt.Errorf("%w: attachments must total at most %d bytes", errAttachmentTooLarge, maxTotalAttachmentBytes)
			}
			attachments = append(attachments, attachment)
		}
	}
	return attachments, nil
}

func readAttachment(header *multipart.FileHeader) (InputResource, error) {
	if header == nil {
		return InputResource{}, http.ErrMissingFile
	}
	if !supportedAttachment(header.Filename) {
		return InputResource{}, fmt.Errorf("%w: %s", errUnsupportedAttachment, header.Filename)
	}
	if header.Size > maxAttachmentBytes {
		return InputResource{}, fmt.Errorf("%w: %s exceeds %d bytes", errAttachmentTooLarge, header.Filename, maxAttachmentBytes)
	}
	file, err := header.Open()
	if err != nil {
		return InputResource{}, err
	}
	defer file.Close()
	content, err := io.ReadAll(io.LimitReader(file, maxAttachmentBytes+1))
	if err != nil {
		return InputResource{}, err
	}
	if len(content) == 0 {
		return InputResource{}, fmt.Errorf("%w: %s is empty", ErrInvalidRequest, header.Filename)
	}
	if len(content) > maxAttachmentBytes {
		return InputResource{}, fmt.Errorf("%w: %s exceeds %d bytes", errAttachmentTooLarge, header.Filename, maxAttachmentBytes)
	}
	digest := sha256.Sum256(content)
	digestHex := hex.EncodeToString(digest[:])
	mimeType := strings.TrimSpace(header.Header.Get("Content-Type"))
	if mimeType == "" || mimeType == "application/octet-stream" {
		mimeType = mime.TypeByExtension(strings.ToLower(filepath.Ext(header.Filename)))
	}
	if mimeType == "" {
		mimeType = http.DetectContentType(content)
	}
	return InputResource{
		ResourceID: "input-" + digestHex[:16],
		Name:       filepath.Base(header.Filename),
		MimeType:   mimeType,
		Content:    content,
		SHA256:     digestHex,
	}, nil
}

func supportedAttachment(name string) bool {
	switch strings.ToLower(filepath.Ext(strings.TrimSpace(name))) {
	case ".pdf", ".txt", ".md", ".markdown", ".csv", ".json", ".yaml", ".yml", ".docx", ".png", ".jpg", ".jpeg", ".webp":
		return true
	default:
		return false
	}
}

func parsePositiveInt64(raw string) (int64, error) {
	if strings.TrimSpace(raw) == "" {
		return 0, nil
	}
	value, err := strconv.ParseInt(raw, 10, 64)
	if err != nil || value <= 0 {
		return 0, errors.New("must be a positive integer")
	}
	return value, nil
}

func parseTaskListLimit(raw string) (int, error) {
	if strings.TrimSpace(raw) == "" {
		return defaultTaskListLimit, nil
	}
	value, err := strconv.Atoi(strings.TrimSpace(raw))
	if err != nil || value <= 0 || value > maxTaskListLimit {
		return 0, fmt.Errorf("limit must be between 1 and %d", maxTaskListLimit)
	}
	return value, nil
}

func respondCreateError(c *gin.Context, err error) {
	var maxBytesError *http.MaxBytesError
	switch {
	case errors.Is(err, errAttachmentTooLarge), errors.As(err, &maxBytesError):
		response.PayloadTooLarge(c, err.Error())
	case errors.Is(err, errUnsupportedAttachment):
		response.UnsupportedMediaType(c, err.Error())
	default:
		response.BadRequest(c, "invalid request", err)
	}
}

func (h *Handler) respondError(c *gin.Context, err error) {
	switch {
	case errors.Is(err, ErrInvalidRequest), errors.Is(err, ErrModelConfigInactive):
		response.BadRequest(c, err.Error(), nil)
	case errors.Is(err, ErrTaskNotFound), errors.Is(err, ErrAgentConfigNotFound), errors.Is(err, ErrModelConfigNotFound), errors.Is(err, ErrArtifactNotFound):
		response.NotFound(c, err.Error())
	case errors.Is(err, ErrClientRequestConflict):
		response.ConflictCode(c, "client_request_conflict", err.Error())
	case errors.Is(err, ErrThreadRunConflict):
		response.ConflictCode(c, "thread_run_conflict", err.Error())
	case errors.Is(err, ErrTaskNotReady):
		response.ConflictCode(c, "run_not_ready", err.Error())
	case errors.Is(err, ErrTaskAlreadyFinished):
		response.Conflict(c, err.Error())
	default:
		response.InternalServerError(c)
	}
}
