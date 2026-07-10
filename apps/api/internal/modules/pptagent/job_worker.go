package pptagent

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"regexp"
	"strings"

	"agent-task-platform/apps/api/internal/shared/actor"
)

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
	stream, err := s.runtimeClient.GenerateDeck(ctx, actor.User{UserID: job.UserID, Username: input.Username}, RuntimeGenerateDeckRequest{
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
	stream, err := s.runtimeClient.ResumeDeck(ctx, actor.User{UserID: job.UserID, Username: input.Username}, RuntimeResumeDeckRequest{
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
