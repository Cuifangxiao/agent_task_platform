package pptagent

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/url"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"agent-task-platform/apps/api/internal/modules/agentconfig"
	"agent-task-platform/apps/api/internal/modules/modelconfig"
	"agent-task-platform/apps/api/internal/shared/actor"
)

var sensitiveErrorPatterns = []*regexp.Regexp{
	regexp.MustCompile(`(?i)bearer\s+[a-z0-9._-]+`),
	regexp.MustCompile(`(?i)(?:api[_-]?key|authorization)\s*[:=]\s*[^\s,;]+`),
	regexp.MustCompile(`\bsk-[A-Za-z0-9_-]{12,}\b`),
}

type Service struct {
	modelConfigRepo modelconfig.Repository
	agentConfigRepo agentconfig.Repository
	runtimeClient   RuntimeClient
	hub             *TaskHub
	artifactDir     string
}

func NewService(
	modelConfigRepo modelconfig.Repository,
	agentConfigRepo agentconfig.Repository,
	runtimeClient RuntimeClient,
	artifactDir string,
	hubs ...*TaskHub,
) *Service {
	if strings.TrimSpace(artifactDir) == "" {
		artifactDir = filepath.Join("apps", "runtime", "artifacts", "decks")
	}
	if absolute, err := filepath.Abs(artifactDir); err == nil {
		artifactDir = absolute
	}
	hub := NewTaskHub(time.Hour)
	if len(hubs) > 0 && hubs[0] != nil {
		hub = hubs[0]
	}
	return &Service{
		modelConfigRepo: modelConfigRepo,
		agentConfigRepo: agentConfigRepo,
		runtimeClient:   runtimeClient,
		hub:             hub,
		artifactDir:     artifactDir,
	}
}

func (s *Service) CreateTask(ctx context.Context, user actor.User, req CreateTaskRequest) (*Task, error) {
	description := strings.TrimSpace(req.Description)
	if user.UserID <= 0 || req.AgentConfigID < 0 || description == "" || len([]rune(description)) > 5000 {
		return nil, fmt.Errorf("%w: user, agent_config_id, or description is invalid", ErrInvalidRequest)
	}
	models, err := s.resolveRuntimeModelConfigs(ctx, user.UserID, req.AgentConfigID)
	if err != nil {
		return nil, err
	}
	taskID, err := randomUUID()
	if err != nil {
		return nil, fmt.Errorf("create task id: %w", err)
	}
	task, err := s.hub.Create(taskID, user.UserID, time.Now().UTC())
	if err != nil {
		return nil, err
	}
	taskContext, cancel := context.WithCancel(context.Background())
	if err := s.hub.SetCancel(taskID, cancel); err != nil {
		cancel()
		return nil, err
	}

	options := normalizeOptions(req.Options)
	runtimeRequest := RuntimeRequest{
		TaskID:      taskID,
		Description: description,
		Attachments: append([]InputResource(nil), req.Attachments...),
		Models:      models,
		Options:     options,
	}
	go s.runTask(taskContext, user.UserID, runtimeRequest)
	return task, nil
}

func (s *Service) GetTask(_ context.Context, ownerID int64, taskID string) (*Task, error) {
	return s.hub.Get(ownerID, normalizeTaskID(taskID))
}

func (s *Service) SubscribeTask(_ context.Context, ownerID int64, taskID string, after uint64) (*Subscription, error) {
	return s.hub.Subscribe(ownerID, normalizeTaskID(taskID), after)
}

func (s *Service) CancelTask(_ context.Context, ownerID int64, taskID string) (*Task, error) {
	taskID = normalizeTaskID(taskID)
	cancel, err := s.hub.Cancel(ownerID, taskID)
	if err != nil {
		return nil, err
	}
	if cancel != nil {
		cancel()
	}
	return s.hub.Get(ownerID, taskID)
}

func (s *Service) runTask(ctx context.Context, ownerID int64, req RuntimeRequest) {
	defer func() {
		if recovered := recover(); recovered != nil {
			s.publishFailure(ownerID, req.TaskID, "GO_WORKER_PANIC", "PPT generation worker stopped unexpectedly", fmt.Sprint(recovered), true)
		}
	}()
	stream, err := s.runtimeClient.GeneratePpt(ctx, req)
	if err != nil {
		s.publishFailure(ownerID, req.TaskID, "PYTHON_RUNTIME_UNAVAILABLE", "PPT generation service is unavailable", safeErrorSummary(err), true)
		return
	}
	defer stream.Close()

	terminal := false
	for {
		event, recvErr := stream.Recv()
		if recvErr != nil {
			if terminal || s.taskIsTerminal(ownerID, req.TaskID) {
				return
			}
			if errors.Is(recvErr, context.Canceled) && ctx.Err() != nil {
				return
			}
			debugMessage := "runtime stream reached EOF before a terminal event"
			if !errors.Is(recvErr, io.EOF) {
				debugMessage = safeErrorSummary(recvErr)
			}
			s.publishFailure(ownerID, req.TaskID, "PYTHON_STREAM_LOST", "PPT generation service connection was interrupted", debugMessage, true)
			return
		}
		if event == nil {
			continue
		}
		if event.TaskID != "" && event.TaskID != req.TaskID {
			s.publishFailure(ownerID, req.TaskID, "INVALID_RUNTIME_EVENT", "PPT generation returned an invalid event", "event task id does not match", false)
			return
		}
		event.TaskID = req.TaskID
		sanitizeRuntimeEvent(event)
		if err := validateAgentEvent(*event); err != nil {
			s.publishFailure(ownerID, req.TaskID, "INVALID_RUNTIME_EVENT", "PPT generation returned an invalid event", safeErrorSummary(err), false)
			return
		}
		s.attachResourceURLs(event)
		if err := s.hub.Publish(req.TaskID, *event); err != nil {
			if errors.Is(err, ErrTaskAlreadyFinished) {
				return
			}
			s.publishFailure(ownerID, req.TaskID, "INVALID_RUNTIME_EVENT", "PPT generation returned an invalid event", safeErrorSummary(err), false)
			return
		}
		terminal = event.Terminal()
		if terminal {
			return
		}
	}
}

func (s *Service) publishFailure(ownerID int64, taskID, code, message, debugMessage string, retryable bool) {
	task, err := s.hub.Get(ownerID, taskID)
	if err != nil || task.Status.Terminal() {
		return
	}
	event := AgentEvent{
		TaskID:    taskID,
		Sequence:  task.LastEventSequence + 1,
		CreatedAt: time.Now().UTC(),
		Type:      EventTaskFailed,
		Stage:     task.Stage,
		Message:   message,
		Error: &ErrorDetail{
			Code:      code,
			Message:   message,
			Retryable: retryable,
		},
	}
	if debugMessage != "" {
		slog.Error("ppt task failed", "task_id", taskID, "code", code, "debug", sanitizeErrorText(debugMessage))
	}
	if publishErr := s.hub.Publish(taskID, event); publishErr != nil && !errors.Is(publishErr, ErrTaskAlreadyFinished) {
		slog.Error("publish ppt task failure", "task_id", taskID, "error", publishErr)
	}
}

func (s *Service) taskIsTerminal(ownerID int64, taskID string) bool {
	task, err := s.hub.Get(ownerID, taskID)
	return err == nil && task.Status.Terminal()
}

func (s *Service) attachResourceURLs(event *AgentEvent) {
	if event == nil {
		return
	}
	for index := range event.Resources {
		event.Resources[index].URL = taskResourceURL(event.TaskID, event.Resources[index])
	}
	if event.FinalResult != nil {
		event.FinalResult.PPTFile.URL = taskResourceURL(event.TaskID, event.FinalResult.PPTFile)
	}
}

func taskResourceURL(taskID string, resource ResourceRef) string {
	if resource.Type == ResourcePPTX {
		return "/api/v1/ppt/tasks/" + url.PathEscape(taskID) + "/file"
	}
	relativePath, err := normalizeArtifactRelativePath(resource.RelativePath)
	if err != nil {
		return ""
	}
	parts := strings.Split(relativePath, "/")
	for index := range parts {
		parts[index] = url.PathEscape(parts[index])
	}
	return "/api/v1/ppt/tasks/" + url.PathEscape(taskID) + "/artifacts/" + strings.Join(parts, "/")
}

func normalizeOptions(options PptOptions) PptOptions {
	if strings.TrimSpace(options.Language) == "" {
		options.Language = "zh-CN"
	}
	if strings.TrimSpace(options.AspectRatio) == "" {
		options.AspectRatio = "16:9"
	}
	if strings.TrimSpace(options.TemplateID) == "" {
		options.TemplateID = "auto"
	}
	return options
}

func safeErrorSummary(err error) string {
	if err == nil {
		return ""
	}
	return sanitizeErrorText(err.Error())
}

func sanitizeRuntimeEvent(event *AgentEvent) {
	if event == nil || event.Error == nil {
		return
	}
	event.Error.Message = sanitizeErrorText(event.Error.Message)
	if event.Error.DebugMessage != "" {
		slog.Error(
			"python ppt runtime reported an error",
			"task_id", event.TaskID,
			"code", event.Error.Code,
			"debug", sanitizeErrorText(event.Error.DebugMessage),
		)
		event.Error.DebugMessage = ""
	}
	if event.Type == EventTaskFailed {
		event.Message = sanitizeErrorText(event.Message)
	}
}

func normalizeTaskID(taskID string) string {
	return strings.ToLower(strings.TrimSpace(taskID))
}

func sanitizeErrorText(value string) string {
	message := strings.Join(strings.Fields(value), " ")
	for _, pattern := range sensitiveErrorPatterns {
		message = pattern.ReplaceAllString(message, "[redacted]")
	}
	runes := []rune(message)
	if len(runes) > 900 {
		message = string(runes[:900])
	}
	return message
}

func randomUUID() (string, error) {
	var value [16]byte
	if _, err := rand.Read(value[:]); err != nil {
		return "", err
	}
	value[6] = (value[6] & 0x0f) | 0x40
	value[8] = (value[8] & 0x3f) | 0x80
	encoded := hex.EncodeToString(value[:])
	return encoded[0:8] + "-" + encoded[8:12] + "-" + encoded[12:16] + "-" + encoded[16:20] + "-" + encoded[20:32], nil
}
