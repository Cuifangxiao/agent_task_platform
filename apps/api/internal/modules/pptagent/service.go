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
	"net/url"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
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

const (
	persistenceTimeout   = 5 * time.Second
	workerReleaseTimeout = 15 * time.Second
	heartbeatInterval    = 30 * time.Second
	workerReconcileTries = 24
)

type Service struct {
	modelConfigRepo modelconfig.Repository
	agentConfigRepo agentconfig.Repository
	runRepo         RunRepository
	runtimeClient   RuntimeClient
	hub             *TaskHub
	artifactDir     string
	transitionMu    [64]sync.Mutex
}

func NewService(
	modelConfigRepo modelconfig.Repository,
	agentConfigRepo agentconfig.Repository,
	runtimeClient RuntimeClient,
	artifactDir string,
	hubs ...*TaskHub,
) *Service {
	return newService(modelConfigRepo, agentConfigRepo, runtimeClient, nil, artifactDir, hubs...)
}

// NewPersistentService configures MySQL as the durable source of truth while
// retaining TaskHub for process-local cancellation and WebSocket fan-out.
func NewPersistentService(
	modelConfigRepo modelconfig.Repository,
	agentConfigRepo agentconfig.Repository,
	runtimeClient RuntimeClient,
	runRepo RunRepository,
	artifactDir string,
	hubs ...*TaskHub,
) *Service {
	return newService(modelConfigRepo, agentConfigRepo, runtimeClient, runRepo, artifactDir, hubs...)
}

func newService(
	modelConfigRepo modelconfig.Repository,
	agentConfigRepo agentconfig.Repository,
	runtimeClient RuntimeClient,
	runRepo RunRepository,
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
		runRepo:         runRepo,
		runtimeClient:   runtimeClient,
		hub:             hub,
		artifactDir:     artifactDir,
	}
}

func (s *Service) CreateTask(ctx context.Context, user actor.User, req CreateTaskRequest) (*Task, error) {
	description := strings.TrimSpace(req.Description)
	name, err := normalizePresentationName(req.Name, description)
	if err != nil {
		return nil, err
	}
	threadID := strings.TrimSpace(req.ThreadID)
	clientRequestID := strings.TrimSpace(req.ClientRequestID)
	if user.UserID <= 0 || req.AgentConfigID < 0 || description == "" || len([]rune(description)) > 5000 || len(threadID) > 128 || len(clientRequestID) > 128 {
		return nil, fmt.Errorf("%w: user, agent_config_id, or description is invalid", ErrInvalidRequest)
	}
	fingerprint, err := createTaskFingerprint(threadID, name, description, req.AgentConfigID, req.Attachments)
	if err != nil {
		return nil, fmt.Errorf("fingerprint task request: %w", err)
	}
	if clientRequestID != "" {
		var existing *Task
		var lookupErr error
		if s.runRepo != nil {
			existing, lookupErr = s.runRepo.FindIdempotent(ctx, user.UserID, clientRequestID, fingerprint)
		} else {
			existing, lookupErr = s.hub.FindIdempotent(user.UserID, clientRequestID, fingerprint)
		}
		if lookupErr != nil || existing != nil {
			return existing, lookupErr
		}
	}
	models, resolvedAgentConfigID, err := s.resolveRuntimeModelConfigs(ctx, user.UserID, req.AgentConfigID)
	if err != nil {
		return nil, err
	}
	if threadID == "" {
		threadID, err = randomUUID()
		if err != nil {
			return nil, fmt.Errorf("create thread id: %w", err)
		}
	}
	runID, err := randomUUID()
	if err != nil {
		return nil, fmt.Errorf("create run id: %w", err)
	}
	now := time.Now().UTC()
	var task *Task
	var unlockCreateTransition func()
	if s.runRepo != nil {
		// The durable row becomes visible before its process-local cancel function
		// is installed. Hold the same transition lock used by CancelTask until the
		// hot worker is fully registered so a concurrent idempotent retry cannot
		// observe a half-initialized run and lose its cancellation intent.
		unlockCreateTransition = s.lockRunTransition(runID)
		defer func() {
			if unlockCreateTransition != nil {
				unlockCreateTransition()
			}
		}()
		seed := Task{
			SchemaVersion:   CurrentSchemaVersion,
			ThreadID:        threadID,
			RunID:           runID,
			TaskID:          runID,
			ClientRequestID: clientRequestID,
			AgentConfigID:   resolvedAgentConfigID,
			Name:            name,
			Description:     description,
			Status:          TaskRunning,
			Stage:           StagePreparing,
			Message:         "PPT task created",
			CreatedAt:       now,
			UpdatedAt:       now,
			ownerID:         user.UserID,
		}
		created := false
		persistCtx, release := persistenceContext(ctx)
		task, created, err = s.runRepo.CreateOrGet(persistCtx, seed, fingerprint)
		release()
		if err != nil {
			if isCreateSemanticError(err) {
				return nil, err
			}
			// Exec/commit errors can be ambiguous when the connection drops after
			// MySQL committed. This run ID is unique to the current invocation, so
			// an exact read proves ownership and prevents an active ghost.
			committed, recovered := s.findCommittedRunAfterCreate(ctx, user.UserID, runID)
			if !recovered {
				return nil, err
			}
			task = committed
			created = true
		}
		if !created {
			return task, nil
		}
		if err := s.hub.Register(*task, fingerprint); err != nil {
			s.failUnstartedRun(task, "GO_WORKER_REGISTRATION_FAILED", "PPT worker could not be registered")
			return nil, fmt.Errorf("register durable ppt run: %w", err)
		}
	} else {
		var existing bool
		task, existing, err = s.hub.CreateRunWithDetails(
			runID,
			threadID,
			clientRequestID,
			fingerprint,
			name,
			description,
			resolvedAgentConfigID,
			user.UserID,
			now,
		)
		if err != nil {
			return nil, err
		}
		if existing {
			return task, nil
		}
	}
	taskContext, cancel := context.WithCancel(context.WithoutCancel(ctx))
	if err := s.hub.SetCancel(runID, cancel); err != nil {
		cancel()
		if s.runRepo != nil {
			s.hub.DiscardUnstarted(runID)
			s.failUnstartedRun(task, "GO_WORKER_INITIALIZATION_FAILED", "PPT worker could not be initialized")
		}
		return nil, err
	}
	if unlockCreateTransition != nil {
		unlockCreateTransition()
		unlockCreateTransition = nil
	}

	runtimeRequest := RuntimeRequest{
		ThreadID:    threadID,
		RunID:       runID,
		TaskID:      runID,
		Description: description,
		Attachments: append([]InputResource(nil), req.Attachments...),
		Models:      models,
	}
	go s.runTask(taskContext, user.UserID, runtimeRequest)
	return task, nil
}

func (s *Service) GetTask(ctx context.Context, ownerID int64, taskID string) (*Task, error) {
	taskID = normalizeTaskID(taskID)
	if s.runRepo != nil {
		return s.runRepo.Get(ctx, ownerID, taskID)
	}
	return s.hub.Get(ownerID, taskID)
}

func (s *Service) ListTasks(ctx context.Context, ownerID int64, threadID string, limit int) ([]*Task, error) {
	threadID = strings.TrimSpace(threadID)
	if s.runRepo != nil {
		return s.runRepo.List(ctx, ownerID, threadID, limit)
	}
	return s.hub.List(ownerID, threadID, limit)
}

func (s *Service) DeleteTask(ctx context.Context, ownerID int64, taskID string) error {
	taskID = normalizeTaskID(taskID)
	if s.runRepo != nil {
		unlock := s.lockRunTransition(taskID)
		defer unlock()
		if err := s.runRepo.Delete(ctx, ownerID, taskID); err != nil {
			return err
		}
		s.hub.Invalidate(taskID)
	} else if err := s.hub.Delete(ownerID, taskID); err != nil {
		return err
	}
	if err := s.removeTaskArtifacts(taskID); err != nil {
		slog.Warn("remove deleted ppt task artifacts", "run_id", taskID, "error", err)
	}
	return nil
}

func (s *Service) SubscribeTask(ctx context.Context, ownerID int64, taskID string, after uint64) (*Subscription, error) {
	taskID = normalizeTaskID(taskID)
	if subscription, err := s.hub.Subscribe(ownerID, taskID, after); err == nil || s.runRepo == nil || !errors.Is(err, ErrTaskNotFound) {
		return subscription, err
	}

	task, err := s.runRepo.Get(ctx, ownerID, taskID)
	if err != nil {
		return nil, err
	}
	if after > task.LastEventSequence {
		return nil, fmt.Errorf("%w: after=%d last_event_sequence=%d", ErrEventCursorAhead, after, task.LastEventSequence)
	}
	if !task.Status.Terminal() {
		return nil, fmt.Errorf("%w: durable run %s has no ready local worker", ErrTaskNotReady, taskID)
	}
	replay, err := s.runRepo.ListEventsAfter(ctx, ownerID, taskID, after)
	if err != nil {
		return nil, err
	}
	expected := after + 1
	for _, event := range replay {
		if event.Sequence != expected {
			return nil, fmt.Errorf("durable event history for run %s is incomplete at sequence %d", taskID, expected)
		}
		expected++
	}
	if expected != task.LastEventSequence+1 {
		return nil, fmt.Errorf("durable event history for run %s ends at sequence %d, want %d", taskID, expected-1, task.LastEventSequence)
	}
	events := make(chan AgentEvent)
	close(events)
	return &Subscription{Replay: replay, Events: events, Close: func() {}}, nil
}

func (s *Service) CancelTask(ctx context.Context, ownerID int64, taskID string) (*Task, error) {
	taskID = normalizeTaskID(taskID)
	if s.runRepo != nil {
		unlock := s.lockRunTransition(taskID)
		defer unlock()

		task, err := s.runRepo.Get(ctx, ownerID, taskID)
		if err != nil {
			return nil, err
		}
		if task.Status.Terminal() {
			return nil, ErrTaskAlreadyFinished
		}
		cancel, err := s.hub.CancelFunc(ownerID, taskID)
		if err != nil {
			return nil, err
		}
		event := AgentEvent{
			TaskID:    taskID,
			Sequence:  task.LastEventSequence + 1,
			CreatedAt: time.Now().UTC(),
			Type:      EventTaskFailed,
			Stage:     task.Stage,
			Message:   "PPT task cancelled",
			Error: &ErrorDetail{
				Code:      "TASK_CANCELLED",
				Message:   "PPT task cancelled",
				Retryable: true,
			},
		}
		persistCtx, release := persistenceContext(ctx)
		committed, canonical, err := s.runRepo.AppendEvent(persistCtx, event, time.Now().UTC())
		release()
		if err != nil {
			// Repository-level commit recovery normally resolves this case. Keep a
			// final service-level reconciliation for a connection that became usable
			// only after the exact event lookup: a durable cancellation is success,
			// even if the original commit acknowledgement was lost.
			lookupCtx, cancelLookup := persistenceContext(ctx)
			reconciled, lookupErr := s.runRepo.Get(lookupCtx, ownerID, taskID)
			cancelLookup()
			if lookupErr == nil && reconciled.Status == TaskCancelled && reconciled.Error != nil && reconciled.Error.Code == "TASK_CANCELLED" {
				workerAlive, workerStateErr := s.hub.WorkerAlive(ownerID, taskID)
				s.hub.Invalidate(taskID)
				if cancel != nil {
					cancel()
				}
				if workerStateErr == nil && !workerAlive {
					if markErr := s.markWorkerDoneWithRetry(taskID); markErr != nil {
						slog.Error("release reconciled cancelled ppt worker", "run_id", taskID, "error", markErr)
					}
				}
				return reconciled, nil
			}
			if lookupErr == nil && !reconciled.Status.Terminal() {
				// The cancellation definitely did not commit. Preserve the local
				// cancel function so the caller can retry without orphaning the worker.
				return nil, err
			}
			// The database is unavailable or moved to a different terminal state.
			// Stop the local worker and close its hot stream; keeping it alive would
			// violate the user's cancellation intent while state is ambiguous.
			s.hub.Invalidate(taskID)
			if cancel != nil {
				cancel()
			}
			if lookupErr == nil && reconciled.Status.Terminal() {
				return nil, ErrTaskAlreadyFinished
			}
			return nil, err
		}
		workerAlive, workerStateErr := s.hub.WorkerAlive(ownerID, taskID)
		if publishErr := s.hub.PublishCommitted(*committed, *canonical); publishErr != nil {
			slog.Error("broadcast committed ppt cancellation", "run_id", taskID, "error", publishErr)
			s.hub.Invalidate(taskID)
		}
		if cancel != nil {
			cancel()
		}
		if workerStateErr == nil && !workerAlive {
			if markErr := s.markWorkerDoneWithRetry(taskID); markErr != nil {
				slog.Error("release already-stopped cancelled ppt worker", "run_id", taskID, "error", markErr)
			}
		}
		return committed, nil
	}
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
	runID := runtimeRequestRunID(req)
	stopHeartbeat := s.startHeartbeat(runID)
	defer func() {
		stopHeartbeat()
		if recovered := recover(); recovered != nil {
			s.publishFailure(ownerID, runID, "GO_WORKER_PANIC", "PPT generation worker stopped unexpectedly", fmt.Sprint(recovered), true)
		}
		if s.runRepo != nil && !s.taskIsTerminal(ownerID, runID) {
			s.publishFailure(ownerID, runID, "GO_WORKER_EXITED", "PPT generation worker stopped before completion", "worker exited without a durable terminal event", true)
		}
		s.hub.WorkerDone(runID)
		if s.runRepo != nil {
			if !s.taskIsTerminal(ownerID, runID) {
				// Keep the durable guard, but continue trying to close the run after a
				// transient database outage. This goroutine is only a single-instance
				// safety net; process restart remains the final recovery boundary.
				go s.reconcileExitedWorker(ownerID, runID)
				return
			}
			if err := s.markWorkerDoneWithRetry(runID); err != nil {
				slog.Error("mark durable ppt worker done", "run_id", runID, "error", err)
			}
		}
	}()
	stream, err := s.runtimeClient.GeneratePpt(ctx, req)
	if err != nil {
		s.publishFailure(ownerID, runID, "PYTHON_RUNTIME_UNAVAILABLE", "PPT generation service is unavailable", safeErrorSummary(err), true)
		return
	}
	defer stream.Close()

	terminal := false
	for {
		event, recvErr := stream.Recv()
		if recvErr != nil {
			if terminal || s.taskIsTerminal(ownerID, runID) {
				return
			}
			if errors.Is(recvErr, context.Canceled) && ctx.Err() != nil {
				return
			}
			debugMessage := "runtime stream reached EOF before a terminal event"
			if !errors.Is(recvErr, io.EOF) {
				debugMessage = safeErrorSummary(recvErr)
			}
			s.publishFailure(ownerID, runID, "PYTHON_STREAM_LOST", "PPT generation service connection was interrupted", debugMessage, true)
			return
		}
		if event == nil {
			continue
		}
		if event.TaskID != "" && normalizeTaskID(event.TaskID) != runID {
			s.publishFailure(ownerID, runID, "INVALID_RUNTIME_EVENT", "PPT generation returned an invalid event", "event task id does not match", false)
			return
		}
		if event.RunID != "" && normalizeTaskID(event.RunID) != runID {
			s.publishFailure(ownerID, runID, "INVALID_RUNTIME_EVENT", "PPT generation returned an invalid event", "event run id does not match", false)
			return
		}
		if event.ThreadID != "" && req.ThreadID != "" && strings.TrimSpace(event.ThreadID) != strings.TrimSpace(req.ThreadID) {
			s.publishFailure(ownerID, runID, "INVALID_RUNTIME_EVENT", "PPT generation returned an invalid event", "event thread id does not match", false)
			return
		}
		event.RunID = runID
		event.TaskID = runID
		if req.ThreadID != "" {
			event.ThreadID = req.ThreadID
		}
		sanitizeRuntimeEvent(event)
		if err := validateAgentEvent(*event); err != nil {
			s.publishFailure(ownerID, runID, "INVALID_RUNTIME_EVENT", "PPT generation returned an invalid event", safeErrorSummary(err), false)
			return
		}
		if s.runRepo != nil {
			resources := event.Resources
			if event.Type == EventTaskCompleted {
				resources = append(append([]ResourceRef(nil), resources...), event.FinalResult.PPTFile)
			}
			if event.Type == EventArtifactReady || event.Type == EventTaskCompleted {
				for _, resource := range resources {
					if err := s.verifyArtifact(runID, resource); err != nil {
						s.publishFailure(ownerID, runID, "ARTIFACT_NOT_READY", "PPT generation reported an artifact that is not safely downloadable", safeErrorSummary(err), true)
						return
					}
				}
			}
		}
		s.attachResourceURLs(event)
		if err := s.publishEvent(ctx, *event); err != nil {
			if errors.Is(err, ErrTaskAlreadyFinished) {
				return
			}
			if errors.Is(err, ErrInvalidRequest) {
				s.publishFailure(ownerID, runID, "INVALID_RUNTIME_EVENT", "PPT generation returned an invalid event", safeErrorSummary(err), false)
			} else {
				s.publishFailure(ownerID, runID, "EVENT_PERSISTENCE_FAILED", "PPT progress could not be saved reliably", safeErrorSummary(err), true)
			}
			return
		}
		terminal = event.Terminal()
		if terminal {
			return
		}
	}
}

func (s *Service) publishFailure(ownerID int64, taskID, code, message, debugMessage string, retryable bool) error {
	lookupCtx, cancel := persistenceContext(context.Background())
	task, err := s.GetTask(lookupCtx, ownerID, taskID)
	cancel()
	if err != nil {
		return err
	}
	if task.Status.Terminal() {
		return nil
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
	if publishErr := s.publishEvent(context.Background(), event); publishErr != nil && !errors.Is(publishErr, ErrTaskAlreadyFinished) {
		slog.Error("publish ppt task failure", "task_id", taskID, "error", publishErr)
		return publishErr
	}
	return nil
}

func (s *Service) taskIsTerminal(ownerID int64, taskID string) bool {
	lookupCtx, cancel := persistenceContext(context.Background())
	task, err := s.GetTask(lookupCtx, ownerID, taskID)
	cancel()
	return err == nil && task.Status.Terminal()
}

func (s *Service) publishEvent(ctx context.Context, event AgentEvent) error {
	if s.runRepo == nil {
		return s.hub.Publish(runtimeRequestRunID(RuntimeRequest{RunID: event.RunID, TaskID: event.TaskID}), event)
	}

	runID := normalizeTaskID(event.RunID)
	if runID == "" {
		runID = normalizeTaskID(event.TaskID)
	}
	unlock := s.lockRunTransition(runID)
	defer unlock()
	persistCtx, cancel := persistenceContext(ctx)
	committed, canonical, err := s.runRepo.AppendEvent(persistCtx, event, time.Now().UTC())
	cancel()
	if err != nil {
		// Errors before COMMIT are rolled back and leave the hot stream/cancel
		// control usable for retry. Only an unresolved COMMIT outcome can have
		// made MySQL diverge from the local stream, so only that case invalidates.
		if errors.Is(err, ErrEventCommitUnknown) {
			s.hub.Invalidate(runID)
		}
		return err
	}
	if err := s.hub.PublishCommitted(*committed, *canonical); err != nil {
		// MySQL has already committed the event. Do not manufacture a second
		// event or advance the sequence again. Invalidate the divergent hot
		// stream so subscribers reconnect and replay the durable history.
		slog.Error("broadcast committed ppt event", "run_id", committed.RunID, "sequence", canonical.Sequence, "error", err)
		s.hub.Invalidate(committed.RunID)
	}
	return nil
}

func (s *Service) failUnstartedRun(task *Task, code, message string) {
	if s.runRepo == nil || task == nil {
		return
	}
	event := AgentEvent{
		TaskID:    task.RunID,
		Sequence:  task.LastEventSequence + 1,
		CreatedAt: time.Now().UTC(),
		Type:      EventTaskFailed,
		Stage:     task.Stage,
		Message:   message,
		Error: &ErrorDetail{
			Code:      code,
			Message:   message,
			Retryable: true,
		},
	}
	persistCtx, cancel := persistenceContext(context.Background())
	_, _, appendErr := s.runRepo.AppendEvent(persistCtx, event, time.Now().UTC())
	cancel()
	if appendErr != nil {
		slog.Error("fail unstarted durable ppt run", "run_id", task.RunID, "error", appendErr)
		// Do not release worker_active without a durable terminal. Startup
		// recovery can close the row safely; releasing here would create a
		// permanent running zombie and admit another worker into the thread.
		return
	}
	if err := s.markWorkerDoneWithRetry(task.RunID); err != nil {
		slog.Error("release unstarted durable ppt worker", "run_id", task.RunID, "error", err)
	}
}

func (s *Service) startHeartbeat(runID string) func() {
	if s.runRepo == nil {
		return func() {}
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		defer close(done)
		ticker := time.NewTicker(heartbeatInterval)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case at := <-ticker.C:
				persistCtx, release := persistenceContext(context.Background())
				if err := s.runRepo.TouchHeartbeat(persistCtx, runID, at.UTC()); err != nil {
					slog.Error("touch durable ppt worker heartbeat", "run_id", runID, "error", err)
				}
				release()
			}
		}
	}()
	return func() {
		cancel()
		<-done
	}
}

func (s *Service) RecoverInterruptedRuns(ctx context.Context) (int, error) {
	if s.runRepo == nil {
		return 0, nil
	}
	return s.runRepo.RecoverInterrupted(ctx, time.Now().UTC())
}

func persistenceContext(parent context.Context) (context.Context, context.CancelFunc) {
	if parent == nil {
		parent = context.Background()
	}
	return context.WithTimeout(context.WithoutCancel(parent), persistenceTimeout)
}

func isCreateSemanticError(err error) bool {
	return errors.Is(err, ErrInvalidRequest) ||
		errors.Is(err, ErrClientRequestConflict) ||
		errors.Is(err, ErrThreadRunConflict)
}

func (s *Service) findCommittedRunAfterCreate(parent context.Context, ownerID int64, runID string) (*Task, bool) {
	if s.runRepo == nil {
		return nil, false
	}
	lookupCtx, cancel := persistenceContext(parent)
	defer cancel()
	delay := 25 * time.Millisecond
	for {
		task, err := s.runRepo.Get(lookupCtx, ownerID, runID)
		if err == nil {
			return task, true
		}
		var ready bool
		delay, ready = waitForPersistenceRetry(lookupCtx, delay)
		if !ready {
			return nil, false
		}
	}
}

func (s *Service) markWorkerDoneWithRetry(runID string) error {
	if s.runRepo == nil {
		return nil
	}
	overallCtx, cancel := context.WithTimeout(context.Background(), workerReleaseTimeout)
	defer cancel()
	delay := 25 * time.Millisecond
	var lastErr error
	for {
		attemptCtx, release := context.WithTimeout(overallCtx, persistenceTimeout)
		lastErr = s.runRepo.MarkWorkerDone(attemptCtx, runID, time.Now().UTC())
		release()
		if lastErr == nil {
			return nil
		}
		if errors.Is(lastErr, ErrInvalidRequest) || errors.Is(lastErr, ErrTaskNotFound) {
			return lastErr
		}
		var ready bool
		delay, ready = waitForPersistenceRetry(overallCtx, delay)
		if !ready {
			return lastErr
		}
	}
}

func (s *Service) reconcileExitedWorker(ownerID int64, runID string) {
	delay := 25 * time.Millisecond
	var lastErr error
	for attempt := 0; attempt < workerReconcileTries; attempt++ {
		lastErr = s.publishFailure(
			ownerID,
			runID,
			"GO_WORKER_EXITED",
			"PPT generation worker stopped before completion",
			"",
			true,
		)
		if s.taskIsTerminal(ownerID, runID) {
			if err := s.markWorkerDoneWithRetry(runID); err != nil {
				slog.Error("release reconciled ppt worker", "run_id", runID, "error", err)
			}
			return
		}
		if attempt+1 == workerReconcileTries {
			break
		}
		delay, _ = waitForPersistenceRetry(context.Background(), delay)
	}
	slog.Error("ppt worker reconciliation exhausted", "run_id", runID, "error", lastErr)
}

func waitForPersistenceRetry(ctx context.Context, delay time.Duration) (time.Duration, bool) {
	if delay <= 0 {
		delay = 25 * time.Millisecond
	}
	timer := time.NewTimer(delay)
	select {
	case <-ctx.Done():
		timer.Stop()
		return delay, false
	case <-timer.C:
	}
	delay *= 2
	if delay > 250*time.Millisecond {
		delay = 250 * time.Millisecond
	}
	return delay, true
}

func (s *Service) lockRunTransition(runID string) func() {
	const (
		fnvOffset = uint32(2166136261)
		fnvPrime  = uint32(16777619)
	)
	hash := fnvOffset
	for index := 0; index < len(runID); index++ {
		hash ^= uint32(runID[index])
		hash *= fnvPrime
	}
	lock := &s.transitionMu[hash%uint32(len(s.transitionMu))]
	lock.Lock()
	return lock.Unlock
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

func createTaskFingerprint(threadID, name, description string, agentConfigID int64, attachments []InputResource) (string, error) {
	type fingerprintAttachment struct {
		ResourceID    string `json:"resource_id"`
		Name          string `json:"name"`
		MimeType      string `json:"mime_type"`
		DeclaredSHA   string `json:"declared_sha256"`
		ContentSHA256 string `json:"content_sha256"`
		SizeBytes     int    `json:"size_bytes"`
	}
	type fingerprintRequest struct {
		ThreadID      string                  `json:"thread_id"`
		AgentConfigID int64                   `json:"agent_config_id"`
		Name          string                  `json:"name"`
		Description   string                  `json:"description"`
		Attachments   []fingerprintAttachment `json:"attachments"`
	}
	items := make([]fingerprintAttachment, 0, len(attachments))
	for _, attachment := range attachments {
		contentDigest := sha256.Sum256(attachment.Content)
		items = append(items, fingerprintAttachment{
			ResourceID:    strings.TrimSpace(attachment.ResourceID),
			Name:          strings.TrimSpace(attachment.Name),
			MimeType:      strings.TrimSpace(attachment.MimeType),
			DeclaredSHA:   strings.ToLower(strings.TrimSpace(attachment.SHA256)),
			ContentSHA256: hex.EncodeToString(contentDigest[:]),
			SizeBytes:     len(attachment.Content),
		})
	}
	payload, err := json.Marshal(fingerprintRequest{
		ThreadID:      strings.TrimSpace(threadID),
		AgentConfigID: agentConfigID,
		Name:          strings.TrimSpace(name),
		Description:   strings.TrimSpace(description),
		Attachments:   items,
	})
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256(payload)
	return hex.EncodeToString(digest[:]), nil
}

func runtimeRequestRunID(req RuntimeRequest) string {
	if runID := normalizeTaskID(req.RunID); runID != "" {
		return runID
	}
	return normalizeTaskID(req.TaskID)
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
