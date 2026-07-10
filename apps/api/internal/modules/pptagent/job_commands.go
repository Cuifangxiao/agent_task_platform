package pptagent

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"agent-task-platform/apps/api/internal/shared/actor"
)

type deckJobInput struct {
	Username        string `json:"username"`
	Description     string `json:"description"`
	StatusMessageID int64  `json:"status_message_id"`
}

func (s *Service) CreateJob(ctx context.Context, user actor.User, req CreateDeckJobRequest) (*DeckJobResponse, error) {
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

func (s *Service) SubscribeJob(ctx context.Context, userID int64, jobID string, afterSequence int64) (DeckStream, error) {
	if s.jobRepo == nil {
		return nil, fmt.Errorf("ppt deck job repository is not configured")
	}
	if _, err := s.jobRepo.GetJobByID(ctx, userID, jobID); err != nil {
		return nil, err
	}
	return &jobEventStream{ctx: ctx, repo: s.jobRepo, userID: userID, jobID: jobID, after: afterSequence}, nil
}

func (s *Service) SubmitJobAction(ctx context.Context, user actor.User, jobID string, req DeckJobActionRequest) (*DeckJobResponse, bool, error) {
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
	exists, err := s.jobRepo.ActionExists(ctx, user.UserID, jobID, key)
	if err != nil {
		return nil, false, err
	}
	if exists {
		return deckJobResponse(job), true, nil
	}
	if job.Status.Terminal() && action != "regenerate_slide" {
		return nil, false, fmt.Errorf("%w: job is already terminal", ErrInvalidRequest)
	}
	if err := validateActionState(job.Status, action); err != nil {
		return nil, false, err
	}
	if action == "approve_outline" && strings.TrimSpace(req.OutlineMarkdown) == "" {
		return nil, false, fmt.Errorf("%w: outline_markdown is required", ErrInvalidRequest)
	}

	created, err := s.jobRepo.CreateAction(ctx, user.UserID, jobID, key, action, payload)
	if err != nil {
		return nil, false, err
	}
	if !created {
		current, getErr := s.jobRepo.GetJobByID(ctx, user.UserID, jobID)
		return deckJobResponse(current), true, getErr
	}
	if action == "approve_outline" {
		if err := s.markOutlineConfirmed(ctx, job.ConversationID, job.RequestID); err != nil {
			_ = s.jobRepo.UpdateActionStatus(ctx, jobID, key, "failed")
			return nil, false, err
		}
	}
	if action == "choose_style" {
		if err := s.markStyleSelected(ctx, job.ConversationID, job.RequestID); err != nil {
			_ = s.jobRepo.UpdateActionStatus(ctx, jobID, key, "failed")
			return nil, false, err
		}
	}
	statusMessageID, err := s.appendResumeMessages(ctx, job.ConversationID, job.RequestID, action)
	if err != nil {
		_ = s.jobRepo.UpdateActionStatus(ctx, jobID, key, "failed")
		return nil, false, err
	}
	if err := s.jobRepo.UpdateJob(ctx, job.ID, DeckJobQueued, job.Stage, job.RequestID, "", "", false); err != nil {
		_ = s.jobRepo.UpdateActionStatus(ctx, jobID, key, "failed")
		return nil, false, err
	}
	_, _ = s.appendJobEvent(context.Background(), job, DeckStreamChunk{
		EventType: "action_accepted", Stage: job.Stage, StageStatus: "pending", Message: "Deck action accepted",
		Severity: "info", Payload: json.RawMessage(payload),
	})
	_ = s.jobRepo.UpdateActionStatus(ctx, jobID, key, "accepted")
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
