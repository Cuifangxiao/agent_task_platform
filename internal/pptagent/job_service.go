package pptagent

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"agent-task-platform/internal/providercredential"
)

type deckJobInput struct {
	Username        string `json:"username"`
	Description     string `json:"description"`
	StatusMessageID int64  `json:"status_message_id"`
}

func (s *Service) CreateJob(ctx context.Context, user providercredential.UserContext, req CreateDeckJobRequest) (*DeckJobResponse, error) {
	if s.jobRepo == nil {
		return nil, fmt.Errorf("ppt deck job repository is not configured")
	}
	description := strings.TrimSpace(req.Description)
	if user.UserID <= 0 || description == "" || len([]rune(description)) > 5000 {
		return nil, fmt.Errorf("%w: invalid user or description", ErrInvalidRequest)
	}
	selectedConfig, err := s.resolvePPTAgentConfig(ctx, user.UserID, req.AgentConfigID)
	if err != nil {
		return nil, err
	}
	files := normalizeSourceFiles(GenerateDeckRequest{SourceFiles: req.SourceFiles})
	conversation, statusMessageID, err := s.startConversation(ctx, user.UserID, selectedConfig, description, sourceFileNames(GenerateDeckRequest{SourceFiles: files}))
	if err != nil {
		return nil, err
	}
	mode := normalizeJobMode(req.Mode)
	input := deckJobInput{Username: user.Username, Description: description, StatusMessageID: statusMessageID}
	inputJSON, _ := json.Marshal(input)
	jobID, err := randomUUID()
	if err != nil {
		return nil, fmt.Errorf("create deck job id: %w", err)
	}
	sources, cleanupSources, err := s.persistJobSources(jobID, files)
	if err != nil {
		return nil, err
	}
	job, err := s.jobRepo.CreateJob(ctx, DeckJob{
		ID: jobID, UserID: user.UserID, ConversationID: conversation.ID, AgentConfigID: selectedConfig.ID,
		Mode: mode, Status: DeckJobQueued, Stage: "brief", InputJSON: string(inputJSON), QualityJSON: "{}",
	}, sources)
	if err != nil {
		cleanupSources()
		return nil, err
	}
	if _, err := s.appendJobEvent(context.Background(), job, DeckStreamChunk{
		EventVersion: DeckEventVersion, EventType: "job_queued", Stage: "brief", StageStatus: "pending",
		Message: "Deck job queued", Severity: "info",
	}); err != nil {
		return nil, err
	}
	s.launchGenerate(job.ID)
	return deckJobResponse(job), nil
}

func (s *Service) GetJob(ctx context.Context, userID int64, jobID string) (*DeckJobResponse, error) {
	if s.jobRepo == nil {
		return nil, fmt.Errorf("ppt deck job repository is not configured")
	}
	job, err := s.jobRepo.GetJobByID(ctx, userID, strings.TrimSpace(jobID))
	if err != nil {
		return nil, err
	}
	return deckJobResponse(job), nil
}

func (s *Service) ProductMetrics(ctx context.Context, userID int64) (*DeckProductMetrics, error) {
	repo, ok := s.jobRepo.(JobMetricsRepository)
	if !ok {
		return nil, fmt.Errorf("ppt deck metrics repository is not configured")
	}
	return repo.ProductMetrics(ctx, userID)
}

func (s *Service) RecordJobSubscriptionResume(ctx context.Context, userID int64, jobID string, after int64) {
	if after <= 0 || s.jobRepo == nil {
		return
	}
	job, err := s.jobRepo.GetJobByID(ctx, userID, jobID)
	if err == nil {
		_, _ = s.appendJobEvent(ctx, job, DeckStreamChunk{EventType: "subscription_resumed", Stage: job.Stage, StageStatus: "running", Message: "SSE subscription resumed", Severity: "info"})
	}
}

func (s *Service) RecordJobSubscriptionDisconnect(ctx context.Context, userID int64, jobID string) {
	if s.jobRepo == nil {
		return
	}
	job, err := s.jobRepo.GetJobByID(ctx, userID, jobID)
	if err == nil && !job.Status.Terminal() && !job.Status.Waiting() {
		_, _ = s.appendJobEvent(ctx, job, DeckStreamChunk{EventType: "subscription_disconnected", Stage: job.Stage, StageStatus: "running", Message: "SSE subscriber disconnected while job continued", Severity: "info"})
	}
}

func (s *Service) RecordArtifactDownload(ctx context.Context, userID int64, requestID string) {
	if s.jobRepo == nil {
		return
	}
	job, err := s.jobRepo.GetJobByRequestID(ctx, userID, requestID)
	if err == nil {
		_, _ = s.appendJobEvent(ctx, job, DeckStreamChunk{EventType: "artifact_downloaded", Stage: "export", StageStatus: "done", Message: "Artifact download served", Severity: "info", RequestID: requestID})
	}
}

func (s *Service) SubscribeJob(ctx context.Context, userID int64, jobID string, afterSequence int64) (DeckStream, error) {
	if s.jobRepo == nil {
		return nil, fmt.Errorf("ppt deck job repository is not configured")
	}
	if _, err := s.jobRepo.GetJobByID(ctx, userID, jobID); err != nil {
		return nil, err
	}
	return &jobEventStream{ctx: ctx, repo: s.jobRepo, userID: userID, jobID: jobID, after: afterSequence}, nil
}

func (s *Service) SubmitJobAction(ctx context.Context, user providercredential.UserContext, jobID string, req DeckJobActionRequest) (*DeckJobResponse, bool, error) {
	if s.jobRepo == nil {
		return nil, false, fmt.Errorf("ppt deck job repository is not configured")
	}
	job, err := s.jobRepo.GetJobByID(ctx, user.UserID, jobID)
	if err != nil {
		return nil, false, err
	}
	action := normalizeResumeAction(req.Action)
	payload := normalizedJSONObject(string(req.Payload))
	key := strings.TrimSpace(req.IdempotencyKey)
	if key == "" {
		sum := sha256.Sum256([]byte(action + "\x00" + strings.TrimSpace(req.OutlineMarkdown) + "\x00" + payload))
		key = hex.EncodeToString(sum[:])
	}
	created, err := s.jobRepo.CreateAction(ctx, user.UserID, jobID, key, action, payload)
	if err != nil {
		return nil, false, err
	}
	if !created {
		current, getErr := s.jobRepo.GetJobByID(ctx, user.UserID, jobID)
		return deckJobResponse(current), true, getErr
	}
	if job.Status.Terminal() && action != "regenerate_slide" {
		_ = s.jobRepo.UpdateActionStatus(ctx, jobID, key, "rejected")
		return nil, false, fmt.Errorf("%w: job is already terminal", ErrInvalidRequest)
	}
	if err := validateActionState(job.Status, action); err != nil {
		_ = s.jobRepo.UpdateActionStatus(ctx, jobID, key, "rejected")
		return nil, false, err
	}
	if action == "approve_outline" {
		if strings.TrimSpace(req.OutlineMarkdown) == "" {
			return nil, false, fmt.Errorf("%w: outline_markdown is required", ErrInvalidRequest)
		}
		if err := s.markOutlineConfirmed(ctx, job.ConversationID, job.RequestID); err != nil {
			return nil, false, err
		}
	}
	if action == "choose_style" {
		if err := s.markStyleSelected(ctx, job.ConversationID, job.RequestID); err != nil {
			return nil, false, err
		}
	}
	statusMessageID, err := s.appendResumeMessages(ctx, job.ConversationID, job.RequestID, action)
	if err != nil {
		return nil, false, err
	}
	if err := s.jobRepo.UpdateJob(ctx, job.ID, DeckJobQueued, job.Stage, job.RequestID, "", "", false); err != nil {
		return nil, false, err
	}
	_, _ = s.appendJobEvent(context.Background(), job, DeckStreamChunk{
		EventType: "action_accepted", Stage: job.Stage, StageStatus: "pending", Message: "Deck action accepted",
		Severity: "info", Payload: json.RawMessage(payload),
	})
	s.launchResume(job.ID, key, action, strings.TrimSpace(req.OutlineMarkdown), payload, statusMessageID)
	updated, err := s.jobRepo.GetJobByID(ctx, user.UserID, jobID)
	return deckJobResponse(updated), false, err
}

func (s *Service) CancelJob(ctx context.Context, userID int64, jobID string) (*DeckJobResponse, error) {
	if s.jobRepo == nil {
		return nil, fmt.Errorf("ppt deck job repository is not configured")
	}
	job, err := s.jobRepo.RequestCancel(ctx, userID, jobID)
	if err != nil {
		return nil, err
	}
	s.cancelMu.Lock()
	cancel := s.jobCancels[jobID]
	s.cancelMu.Unlock()
	if cancel != nil {
		cancel()
	}
	_, _ = s.appendJobEvent(context.Background(), job, DeckStreamChunk{
		EventType: "cancelled", Stage: job.Stage, StageStatus: "error", Message: "Deck job cancelled",
		Severity: "warning", ErrorCode: "cancelled",
	})
	_ = s.historyRepo.UpdateConversationState(context.Background(), job.ConversationID, ConversationStatusCancelled, job.RequestID)
	return deckJobResponse(job), nil
}

func (s *Service) RecoverJobs(ctx context.Context) error {
	if s.jobRepo == nil {
		return nil
	}
	jobs, err := s.jobRepo.ListRecoverableJobs(ctx)
	if err != nil {
		return err
	}
	for _, job := range jobs {
		if job.Status == DeckJobQueued && job.RequestID == "" {
			s.launchGenerate(job.ID)
			continue
		}
		// A process restart terminates the old gRPC call. Preserve the real stage and expose a retryable failure.
		_ = s.failJob(context.Background(), job, "worker_restarted", "The deck worker restarted while this stage was running.", true)
	}
	return nil
}

func (s *Service) launchGenerate(jobID string) {
	go s.runGenerate(jobID)
}

func (s *Service) runGenerate(jobID string) {
	job, input, err := s.loadJobInput(jobID)
	if err != nil {
		slog.Error("load deck job", "job_id", jobID, "error", err)
		return
	}
	ctx, done := s.workerContext(jobID)
	defer done()
	if err := s.jobRepo.UpdateJob(ctx, jobID, DeckJobRunning, "brief", job.RequestID, "", "", false); err != nil {
		return
	}
	selectedConfig, err := s.resolvePPTAgentConfig(ctx, job.UserID, job.AgentConfigID)
	if err != nil {
		_ = s.failJob(context.Background(), job, "agent_config", err.Error(), false)
		return
	}
	models, err := s.runtimeModelsFromConfigByValue(ctx, job.UserID, selectedConfig)
	if err != nil {
		_ = s.failJob(context.Background(), job, "model_config", err.Error(), false)
		return
	}
	sources, err := s.jobRepo.ListSources(ctx, jobID)
	if err != nil {
		_ = s.failJob(context.Background(), job, "source_load", err.Error(), true)
		return
	}
	files := make([]SourceFile, 0, len(sources))
	for _, source := range sources {
		file, sourceErr := s.runtimeSourceFile(source)
		if sourceErr != nil {
			_ = s.failJob(context.Background(), job, "source_load", sourceErr.Error(), true)
			return
		}
		files = append(files, file)
	}
	stream, err := s.runtimeClient.GenerateDeck(ctx, providercredential.UserContext{UserID: job.UserID, Username: input.Username}, RuntimeGenerateDeckRequest{
		JobID: job.ID, Mode: job.Mode, Description: input.Description, SourceFiles: files, NodeModelConfigs: models,
	})
	if err != nil {
		_ = s.failJob(context.Background(), job, "runtime_start", err.Error(), true)
		return
	}
	projection := newPersistedDeckStream(context.Background(), s.historyRepo, job.ConversationID, input.StatusMessageID, stream)
	s.consumeRuntime(job, stream, projection)
}

func (s *Service) launchResume(jobID, actionKey, action, outline, payload string, statusMessageID int64) {
	go s.runResume(jobID, actionKey, action, outline, payload, statusMessageID)
}

func (s *Service) runResume(jobID, actionKey, action, outline, payload string, statusMessageID int64) {
	job, input, err := s.loadJobInput(jobID)
	if err != nil {
		return
	}
	ctx, done := s.workerContext(jobID)
	defer done()
	if err := s.jobRepo.UpdateJob(ctx, jobID, DeckJobRunning, job.Stage, job.RequestID, "", "", false); err != nil {
		return
	}
	selectedConfig, err := s.resolvePPTAgentConfig(ctx, job.UserID, job.AgentConfigID)
	if err != nil {
		_ = s.failJob(context.Background(), job, "agent_config", err.Error(), false)
		return
	}
	models, err := s.runtimeModelsFromConfigByValue(ctx, job.UserID, selectedConfig)
	if err != nil {
		_ = s.failJob(context.Background(), job, "model_config", err.Error(), false)
		return
	}
	stream, err := s.runtimeClient.ResumeDeck(ctx, providercredential.UserContext{UserID: job.UserID, Username: input.Username}, RuntimeResumeDeckRequest{
		JobID: job.ID, RequestID: job.RequestID, OutlineMarkdown: outline, Action: action, PayloadJSON: payload, NodeModelConfigs: models,
	})
	if err != nil {
		_ = s.jobRepo.UpdateActionStatus(context.Background(), jobID, actionKey, "failed")
		_ = s.failJob(context.Background(), job, "runtime_resume", err.Error(), true)
		return
	}
	projection := newPersistedDeckStream(context.Background(), s.historyRepo, job.ConversationID, statusMessageID, stream)
	s.consumeRuntime(job, stream, projection)
	actionStatus := "done"
	if current, getErr := s.jobRepo.GetJobForWorker(context.Background(), jobID); getErr == nil {
		if current.Status == DeckJobFailed {
			actionStatus = "failed"
		} else if current.Status == DeckJobCancelled {
			actionStatus = "cancelled"
		}
	}
	_ = s.jobRepo.UpdateActionStatus(context.Background(), jobID, actionKey, actionStatus)
}

func (s *Service) consumeRuntime(job *DeckJob, stream DeckStream, projection *persistedDeckStream) {
	defer stream.Close()
	stage := job.Stage
	terminal := false
	for {
		chunk, err := stream.Recv()
		if err != nil {
			if errors.Is(err, io.EOF) && terminal {
				return
			}
			current, getErr := s.jobRepo.GetJobForWorker(context.Background(), job.ID)
			if getErr == nil && (current.CancelRequested || current.Status == DeckJobCancelled) {
				return
			}
			code := "runtime_transport"
			message := "The deck runtime stream ended before a terminal event."
			if err != nil && !errors.Is(err, io.EOF) {
				message = safeErrorSummary(err)
			}
			job.Stage = stage
			_ = s.failJob(context.Background(), job, code, message, true)
			return
		}
		if chunk == nil {
			continue
		}
		if chunk.Stage != "" {
			stage = normalizeDeckStage(chunk.Stage)
		}
		chunk.Stage = stage
		chunk.JobID = job.ID
		chunk.ConversationID = job.ConversationID
		if chunk.EventVersion == 0 {
			chunk.EventVersion = DeckEventVersion
		}
		if chunk.RequestID != "" {
			job.RequestID = chunk.RequestID
		}
		normalizeChunkProtocol(chunk)
		if _, err := s.appendJobEvent(context.Background(), job, *chunk); err != nil {
			_ = s.failJob(context.Background(), job, "event_persist", err.Error(), true)
			return
		}
		// Conversation messages are a compact compatibility projection; the append-only event table is authoritative.
		_ = projection.persistChunk(chunk)
		_ = s.persistAssetFromChunk(context.Background(), job, chunk)
		if chunk.EventType == "artifact" && len(chunk.Payload) > 0 {
			job.QualityJSON = string(chunk.Payload)
			_ = s.jobRepo.UpdateJobQuality(context.Background(), job.ID, job.QualityJSON)
		}
		status := jobStatusFromChunk(chunk)
		if chunk.EventType == "done" && chunk.Done {
			status = finalJobStatus(json.RawMessage(job.QualityJSON))
		}
		if status != "" {
			job.Status = status
			_ = s.jobRepo.UpdateJob(context.Background(), job.ID, status, stage, job.RequestID, chunk.ErrorCode, chunk.Error, chunk.Retryable)
			terminal = status.Terminal() || status.Waiting()
		}
		if chunk.Done && terminal {
			return
		}
	}
}

func (s *Service) appendJobEvent(ctx context.Context, job *DeckJob, chunk DeckStreamChunk) (*DeckJobEvent, error) {
	payload := []byte(chunk.Payload)
	return s.jobRepo.AppendEvent(ctx, DeckJobEvent{
		EventVersion: chunk.EventVersion, JobID: job.ID, RequestID: chunk.RequestID, ConversationID: job.ConversationID,
		EventType: chunk.EventType, Stage: chunk.Stage, StageStatus: chunk.StageStatus, Message: chunk.Message,
		ProgressCurrent: progressCurrent(chunk.Progress), ProgressTotal: progressTotal(chunk.Progress),
		SlideIndex: slideIndex(chunk.Slide), SlideVersion: slideVersion(chunk.Slide), Severity: chunk.Severity,
		ActionRequired: chunk.ActionRequired, Retryable: chunk.Retryable, ErrorCode: chunk.ErrorCode, Payload: payload,
	})
}

func (s *Service) failJob(ctx context.Context, job *DeckJob, code, summary string, retryable bool) error {
	if job == nil {
		return nil
	}
	summary = safeErrorSummary(errors.New(summary))
	if err := s.jobRepo.UpdateJob(ctx, job.ID, DeckJobFailed, job.Stage, job.RequestID, code, summary, retryable); err != nil {
		return err
	}
	_, err := s.appendJobEvent(ctx, job, DeckStreamChunk{
		EventType: "error", Stage: job.Stage, StageStatus: "error", Message: summary, Error: summary,
		ErrorCode: code, Severity: "error", Retryable: retryable, Done: true,
	})
	_ = s.historyRepo.UpdateConversationState(ctx, job.ConversationID, ConversationStatusError, job.RequestID)
	return err
}

func (s *Service) loadJobInput(jobID string) (*DeckJob, deckJobInput, error) {
	job, err := s.jobRepo.GetJobForWorker(context.Background(), jobID)
	if err != nil {
		return nil, deckJobInput{}, err
	}
	var input deckJobInput
	if err := json.Unmarshal([]byte(job.InputJSON), &input); err != nil {
		return nil, deckJobInput{}, fmt.Errorf("decode deck job input: %w", err)
	}
	return job, input, nil
}

func (s *Service) workerContext(jobID string) (context.Context, func()) {
	ctx, cancel := context.WithCancel(context.Background())
	s.cancelMu.Lock()
	s.jobCancels[jobID] = cancel
	s.cancelMu.Unlock()
	return ctx, func() {
		cancel()
		s.cancelMu.Lock()
		delete(s.jobCancels, jobID)
		s.cancelMu.Unlock()
	}
}

type jobEventStream struct {
	ctx     context.Context
	repo    JobRepository
	userID  int64
	jobID   string
	after   int64
	pending []DeckJobEvent
}

func (s *jobEventStream) Recv() (*DeckStreamChunk, error) {
	for {
		if len(s.pending) > 0 {
			event := s.pending[0]
			s.pending = s.pending[1:]
			s.after = event.Sequence
			return eventToChunk(event), nil
		}
		events, err := s.repo.ListEvents(s.ctx, s.userID, s.jobID, s.after, 200)
		if err != nil {
			return nil, err
		}
		if len(events) > 0 {
			s.pending = events
			continue
		}
		job, err := s.repo.GetJobByID(s.ctx, s.userID, s.jobID)
		if err != nil {
			return nil, err
		}
		if job.Status.Terminal() || job.Status.Waiting() {
			return nil, io.EOF
		}
		select {
		case <-s.ctx.Done():
			return nil, s.ctx.Err()
		case <-time.After(350 * time.Millisecond):
		}
	}
}

func (s *jobEventStream) Close() error { return nil }

func eventToChunk(event DeckJobEvent) *DeckStreamChunk {
	chunk := &DeckStreamChunk{
		EventVersion: event.EventVersion, JobID: event.JobID, ConversationID: event.ConversationID,
		RequestID: event.RequestID, EventType: event.EventType, Stage: event.Stage, StageStatus: event.StageStatus,
		Message: event.Message, Severity: event.Severity, ActionRequired: event.ActionRequired, Retryable: event.Retryable,
		ErrorCode: event.ErrorCode, Payload: json.RawMessage(event.Payload), Sequence: event.Sequence,
	}
	if event.ProgressCurrent > 0 || event.ProgressTotal > 0 {
		chunk.Progress = &DeckProgress{Current: event.ProgressCurrent, Total: event.ProgressTotal}
	}
	if event.SlideIndex > 0 {
		chunk.Slide = &DeckSlideEvent{Index: event.SlideIndex, Version: event.SlideVersion}
	}
	if event.EventType == "error" {
		chunk.Error = event.Message
	}
	switch event.EventType {
	case "interrupt", "done", "error", "cancelled":
		chunk.Done = true
	}
	return chunk
}

func normalizeChunkProtocol(chunk *DeckStreamChunk) {
	if chunk.StageStatus == "" {
		switch chunk.EventType {
		case "interrupt":
			chunk.StageStatus = "waiting"
		case "done", "artifact":
			chunk.StageStatus = "done"
		case "error":
			chunk.StageStatus = "error"
		default:
			chunk.StageStatus = "running"
		}
	}
	if chunk.Severity == "" {
		if chunk.Error != "" || chunk.StageStatus == "error" {
			chunk.Severity = "error"
		} else if payloadHasWarnings(chunk.Payload) {
			chunk.Severity = "warning"
		} else {
			chunk.Severity = "info"
		}
	}
	if chunk.ActionRequired == "" && chunk.EventType == "interrupt" {
		if chunk.Stage == "style_review" {
			chunk.ActionRequired = "choose_style"
		} else {
			chunk.ActionRequired = "approve_outline"
		}
	}
	if chunk.Progress == nil || chunk.Slide == nil {
		var payload map[string]any
		_ = json.Unmarshal(chunk.Payload, &payload)
		if chunk.Progress == nil {
			current, total := payloadInt(payload["current"]), payloadInt(payload["total"])
			if current > 0 || total > 0 {
				chunk.Progress = &DeckProgress{Current: current, Total: total}
			}
		}
		if chunk.Slide == nil {
			index := payloadInt(payload["index"])
			if index > 0 {
				chunk.Slide = &DeckSlideEvent{Index: index, Version: payloadInt(payload["version"])}
			}
		}
	}
}

func jobStatusFromChunk(chunk *DeckStreamChunk) DeckJobStatus {
	if chunk.EventType == "interrupt" {
		if chunk.Stage == "style_review" {
			return DeckJobWaitingForStyle
		}
		return DeckJobWaitingForOutline
	}
	if chunk.EventType == "error" {
		return DeckJobFailed
	}
	if chunk.EventType == "artifact" {
		return DeckJobExporting
	}
	if chunk.Done && chunk.EventType == "done" {
		return finalJobStatus(chunk.Payload)
	}
	if chunk.Stage == "export" && !chunk.Done {
		return DeckJobExporting
	}
	return DeckJobRunning
}

func finalJobStatus(quality json.RawMessage) DeckJobStatus {
	if payloadNeedsReview(quality) {
		return DeckJobNeedsReview
	}
	if payloadHasWarnings(quality) {
		return DeckJobSuccessWithWarnings
	}
	return DeckJobSuccess
}

func normalizeDeckStage(stage string) string {
	switch strings.TrimSpace(stage) {
	case "slides", "write_slides":
		return "writer"
	case "design_slides", "render":
		return "designer"
	case "review":
		return "review_vlm"
	case "done":
		return "export"
	default:
		return strings.TrimSpace(stage)
	}
}

func validateActionState(status DeckJobStatus, action string) error {
	switch action {
	case "approve_outline", "regenerate_outline":
		if status != DeckJobWaitingForOutline {
			return fmt.Errorf("%w: outline approval is not expected", ErrInvalidRequest)
		}
	case "choose_style":
		if status != DeckJobWaitingForStyle {
			return fmt.Errorf("%w: style selection is not expected", ErrInvalidRequest)
		}
	case "regenerate_slide":
		if status != DeckJobSuccess && status != DeckJobSuccessWithWarnings && status != DeckJobNeedsReview {
			return fmt.Errorf("%w: slide regeneration requires an exported deck", ErrInvalidRequest)
		}
	default:
		return fmt.Errorf("%w: unsupported deck action %q", ErrInvalidRequest, action)
	}
	return nil
}

func normalizeJobMode(mode string) string {
	if strings.EqualFold(strings.TrimSpace(mode), "quick") {
		return "quick"
	}
	return "step_by_step"
}

func deckJobResponse(job *DeckJob) *DeckJobResponse {
	if job == nil {
		return nil
	}
	var quality any = map[string]any{}
	_ = json.Unmarshal([]byte(job.QualityJSON), &quality)
	return &DeckJobResponse{
		ID: job.ID, ConversationID: job.ConversationID, AgentConfigID: job.AgentConfigID, RequestID: job.RequestID,
		Mode: job.Mode, Status: job.Status, Stage: job.Stage, ErrorCode: job.ErrorCode, ErrorSummary: job.ErrorSummary,
		Retryable: job.Retryable, LastEventSequence: job.LastEventSequence, Quality: quality,
		CreatedAt: job.CreatedAt.Format(time.RFC3339), UpdatedAt: job.UpdatedAt.Format(time.RFC3339),
	}
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

func (s *Service) persistJobSources(jobID string, files []SourceFile) ([]DeckJobSource, func(), error) {
	noop := func() {}
	if len(files) == 0 {
		return nil, noop, nil
	}
	root := filepath.Join(filepath.Dir(s.artifactDir), "deck-sources")
	jobDir := filepath.Join(root, jobID)
	if err := os.MkdirAll(jobDir, 0o700); err != nil {
		return nil, noop, fmt.Errorf("create deck source directory: %w", err)
	}
	cleanup := func() { _ = os.RemoveAll(jobDir) }
	sources := make([]DeckJobSource, 0, len(files))
	for _, file := range files {
		resourceID, err := randomUUID()
		if err != nil {
			cleanup()
			return nil, noop, fmt.Errorf("create deck source id: %w", err)
		}
		ext := strings.ToLower(filepath.Ext(file.FileName))
		if len(ext) > 12 || !regexp.MustCompile(`^\.[a-z0-9]+$`).MatchString(ext) {
			ext = ""
		}
		name := resourceID + ext
		path := filepath.Join(jobDir, name)
		if err := os.WriteFile(path, file.Content, 0o600); err != nil {
			cleanup()
			return nil, noop, fmt.Errorf("persist deck source: %w", err)
		}
		digest := sha256.Sum256(file.Content)
		sources = append(sources, DeckJobSource{
			FileName: file.FileName, ResourceID: resourceID,
			RelativePath: filepath.Join(jobID, name), SHA256: hex.EncodeToString(digest[:]), SizeBytes: int64(len(file.Content)),
		})
	}
	return sources, cleanup, nil
}

func (s *Service) runtimeSourceFile(source DeckJobSource) (SourceFile, error) {
	if strings.TrimSpace(source.RelativePath) == "" {
		return SourceFile{FileName: source.FileName, Content: source.Content, SizeBytes: int64(len(source.Content))}, nil
	}
	root := filepath.Join(filepath.Dir(s.artifactDir), "deck-sources")
	path := filepath.Join(root, filepath.Clean(source.RelativePath))
	rel, err := filepath.Rel(root, path)
	if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) || filepath.IsAbs(rel) {
		return SourceFile{}, fmt.Errorf("invalid deck source resource path")
	}
	info, err := os.Stat(path)
	if err != nil || !info.Mode().IsRegular() {
		return SourceFile{}, fmt.Errorf("deck source resource is unavailable")
	}
	if source.SizeBytes > 0 && info.Size() != source.SizeBytes {
		return SourceFile{}, fmt.Errorf("deck source resource size mismatch")
	}
	return SourceFile{
		FileName: source.FileName, ResourceID: source.ResourceID, LocalPath: path,
		SHA256: source.SHA256, SizeBytes: info.Size(),
	}, nil
}

func safeErrorSummary(err error) string {
	message := strings.TrimSpace(err.Error())
	message = strings.ReplaceAll(message, "\n", " ")
	for _, pattern := range []*regexp.Regexp{
		regexp.MustCompile(`(?i)bearer\s+[a-z0-9._-]+`),
		regexp.MustCompile(`(?i)(?:api[_-]?key|authorization)\s*[:=]\s*[^\s,;]+`),
		regexp.MustCompile(`\bsk-[A-Za-z0-9_-]{12,}\b`),
	} {
		message = pattern.ReplaceAllString(message, "[redacted]")
	}
	if len([]rune(message)) > 900 {
		message = string([]rune(message)[:900])
	}
	return message
}

func payloadHasWarnings(raw json.RawMessage) bool {
	var payload map[string]any
	if json.Unmarshal(raw, &payload) != nil {
		return false
	}
	if warnings, ok := payload["warnings"].([]any); ok && len(warnings) > 0 {
		return true
	}
	return strings.EqualFold(payloadString(payload["status"]), "warning") ||
		strings.EqualFold(payloadString(payload["quality_status"]), "success_with_warnings") ||
		payload["degraded"] == true
}

func payloadNeedsReview(raw json.RawMessage) bool {
	var payload map[string]any
	if json.Unmarshal(raw, &payload) != nil {
		return false
	}
	return payload["needs_review"] == true || strings.EqualFold(payloadString(payload["quality_status"]), "needs_review")
}

func (s *Service) persistAssetFromChunk(ctx context.Context, job *DeckJob, chunk *DeckStreamChunk) error {
	if chunk == nil || chunk.RequestID == "" || len(chunk.Payload) == 0 {
		return nil
	}
	var payload map[string]any
	if json.Unmarshal(chunk.Payload, &payload) != nil {
		return nil
	}
	if chunk.EventType == "slide_preview" {
		for _, kind := range []string{"png", "html"} {
			path := payloadString(payload[kind])
			if path == "" {
				continue
			}
			_ = s.jobRepo.UpsertAsset(ctx, DeckJobAsset{JobID: job.ID, RequestID: chunk.RequestID, Kind: kind,
				SlideIndex: payloadInt(payload["index"]), Version: maxInt(1, payloadInt(payload["version"])), RelativePath: path,
				QualityStatus: payloadString(payload["status"]), MetadataJSON: string(chunk.Payload)})
		}
	}
	if chunk.EventType == "artifact" {
		path := payloadString(payload["relative_path"])
		if path == "" {
			path = "deck.pptx"
		}
		return s.jobRepo.UpsertAsset(ctx, DeckJobAsset{JobID: job.ID, RequestID: chunk.RequestID, Kind: "pptx",
			Version: 1, RelativePath: path, QualityStatus: qualityStatusForPayload(chunk.Payload), MetadataJSON: string(chunk.Payload)})
	}
	return nil
}

func qualityStatusForPayload(raw json.RawMessage) string {
	if payloadNeedsReview(raw) {
		return "needs_review"
	}
	if payloadHasWarnings(raw) {
		return "warning"
	}
	return "ready"
}

func progressCurrent(progress *DeckProgress) int {
	if progress == nil {
		return 0
	}
	return progress.Current
}
func progressTotal(progress *DeckProgress) int {
	if progress == nil {
		return 0
	}
	return progress.Total
}
func slideIndex(slide *DeckSlideEvent) int {
	if slide == nil {
		return 0
	}
	return slide.Index
}
func slideVersion(slide *DeckSlideEvent) int {
	if slide == nil {
		return 0
	}
	return slide.Version
}
func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}
