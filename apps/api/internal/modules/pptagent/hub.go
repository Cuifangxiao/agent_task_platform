package pptagent

import (
	"context"
	"fmt"
	"sort"
	"strings"
	"sync"
	"time"
)

const (
	subscriberBuffer     = 64
	defaultTaskListLimit = 20
	maxTaskListLimit     = 100
)

type taskStream struct {
	task        Task
	events      []AgentEvent
	subscribers map[chan AgentEvent]struct{}
	workerAlive bool
}

type idempotencyKey struct {
	ownerID         int64
	clientRequestID string
}

type idempotencyRecord struct {
	taskID      string
	fingerprint string
}

// TaskHub owns process-local worker controls and live WebSocket fan-out. When a
// RunRepository is configured, MySQL is the durable source of truth and this
// hub is only a bounded hot cache.
type TaskHub struct {
	mu          sync.Mutex
	tasks       map[string]*taskStream
	idempotency map[idempotencyKey]idempotencyRecord
	ttl         time.Duration
}

func NewTaskHub(ttl time.Duration) *TaskHub {
	if ttl <= 0 {
		ttl = time.Hour
	}
	return &TaskHub{
		tasks:       make(map[string]*taskStream),
		idempotency: make(map[idempotencyKey]idempotencyRecord),
		ttl:         ttl,
	}
}

func (h *TaskHub) Create(taskID string, ownerID int64, now time.Time) (*Task, error) {
	task, _, err := h.CreateRun(taskID, taskID, "", "", ownerID, now)
	return task, err
}

// CreateRun atomically enforces idempotency and the one-active-run-per-thread
// invariant. The bool result reports whether an existing idempotent run was
// returned, in which case the caller must not start another runtime worker.
func (h *TaskHub) CreateRun(runID, threadID, clientRequestID, fingerprint string, ownerID int64, now time.Time) (*Task, bool, error) {
	return h.createRun(runID, threadID, clientRequestID, fingerprint, "", 0, ownerID, now)
}

func (h *TaskHub) CreateRunWithDetails(runID, threadID, clientRequestID, fingerprint, description string, agentConfigID, ownerID int64, now time.Time) (*Task, bool, error) {
	return h.createRun(runID, threadID, clientRequestID, fingerprint, description, agentConfigID, ownerID, now)
}

func (h *TaskHub) createRun(runID, threadID, clientRequestID, fingerprint, description string, agentConfigID, ownerID int64, now time.Time) (*Task, bool, error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.pruneLocked(now)
	runID = normalizeTaskID(runID)
	threadID = strings.TrimSpace(threadID)
	clientRequestID = strings.TrimSpace(clientRequestID)
	if runID == "" || threadID == "" || ownerID <= 0 {
		return nil, false, fmt.Errorf("%w: run id, thread id, and owner are required", ErrInvalidRequest)
	}
	if clientRequestID != "" {
		if fingerprint == "" {
			return nil, false, fmt.Errorf("%w: request fingerprint is required with client_request_id", ErrInvalidRequest)
		}
		if existing, err := h.lookupIdempotentLocked(ownerID, clientRequestID, fingerprint); err != nil || existing != nil {
			return existing, existing != nil, err
		}
	}
	if _, exists := h.tasks[runID]; exists {
		return nil, false, fmt.Errorf("%w: duplicate run id", ErrInvalidRequest)
	}
	for _, candidate := range h.tasks {
		if candidate.task.ownerID == ownerID && candidate.task.ThreadID == threadID && candidate.workerAlive {
			return nil, false, fmt.Errorf("%w: thread_id=%s", ErrThreadRunConflict, threadID)
		}
	}
	stream := &taskStream{
		task: Task{
			SchemaVersion:   CurrentSchemaVersion,
			ThreadID:        threadID,
			RunID:           runID,
			TaskID:          runID,
			ClientRequestID: clientRequestID,
			AgentConfigID:   agentConfigID,
			Description:     strings.TrimSpace(description),
			Status:          TaskRunning,
			Stage:           StagePreparing,
			Message:         "PPT task created",
			CreatedAt:       now,
			UpdatedAt:       now,
			ownerID:         ownerID,
		},
		subscribers: make(map[chan AgentEvent]struct{}),
		workerAlive: true,
	}
	h.tasks[runID] = stream
	if clientRequestID != "" {
		h.idempotency[idempotencyKey{ownerID: ownerID, clientRequestID: clientRequestID}] = idempotencyRecord{
			taskID:      runID,
			fingerprint: fingerprint,
		}
	}
	return cloneTask(&stream.task), false, nil
}

// Register installs a run that has already been committed to the durable
// repository. It is intentionally separate from CreateRun so a database
// uniqueness constraint, rather than process-local state, arbitrates
// concurrent idempotency and same-thread creation.
func (h *TaskHub) Register(task Task, fingerprint string) error {
	h.mu.Lock()
	defer h.mu.Unlock()
	now := time.Now().UTC()
	h.pruneLocked(now)

	task.RunID = normalizeTaskID(task.RunID)
	if task.RunID == "" {
		task.RunID = normalizeTaskID(task.TaskID)
	}
	task.TaskID = task.RunID
	task.ThreadID = strings.TrimSpace(task.ThreadID)
	task.ClientRequestID = strings.TrimSpace(task.ClientRequestID)
	if task.RunID == "" || task.ThreadID == "" || task.ownerID <= 0 || task.Status != TaskRunning {
		return fmt.Errorf("%w: durable run snapshot is invalid", ErrInvalidRequest)
	}
	if task.ClientRequestID != "" && strings.TrimSpace(fingerprint) == "" {
		return fmt.Errorf("%w: request fingerprint is required with client_request_id", ErrInvalidRequest)
	}
	if _, exists := h.tasks[task.RunID]; exists {
		return fmt.Errorf("%w: duplicate run id", ErrInvalidRequest)
	}
	for _, candidate := range h.tasks {
		if candidate.task.ownerID == task.ownerID && candidate.task.ThreadID == task.ThreadID && candidate.workerAlive {
			return fmt.Errorf("%w: thread_id=%s", ErrThreadRunConflict, task.ThreadID)
		}
	}

	task.cancel = nil
	h.tasks[task.RunID] = &taskStream{
		task:        task,
		subscribers: make(map[chan AgentEvent]struct{}),
		workerAlive: true,
	}
	if task.ClientRequestID != "" {
		h.idempotency[idempotencyKey{ownerID: task.ownerID, clientRequestID: task.ClientRequestID}] = idempotencyRecord{
			taskID:      task.RunID,
			fingerprint: fingerprint,
		}
	}
	return nil
}

// DiscardUnstarted rolls back a local registration when durable creation
// succeeded but the worker could not be initialized. No accepted event may
// exist at this point.
func (h *TaskHub) DiscardUnstarted(taskID string) {
	h.mu.Lock()
	defer h.mu.Unlock()
	taskID = normalizeTaskID(taskID)
	stream, ok := h.tasks[taskID]
	if !ok || len(stream.events) != 0 {
		return
	}
	h.deleteTaskLocked(taskID)
}

// Invalidate evicts a run from the process-local hot layer. Durable state is
// unaffected; callers can reload the run and its event history from the
// repository on the next request. Any live subscribers are closed so they do
// not remain attached to a snapshot that can no longer receive events.
func (h *TaskHub) Invalidate(taskID string) {
	h.mu.Lock()
	taskID = normalizeTaskID(taskID)
	var cancel context.CancelFunc
	if stream, ok := h.tasks[taskID]; ok {
		cancel = stream.task.cancel
	}
	h.deleteTaskLocked(taskID)
	h.mu.Unlock()
	// A non-terminal stream can no longer safely publish or be cancelled after
	// eviction. Stop its runtime so the worker defer path commits a durable
	// failure rather than letting an unreachable run continue in the background.
	if cancel != nil {
		cancel()
	}
}

func (h *TaskHub) FindIdempotent(ownerID int64, clientRequestID, fingerprint string) (*Task, error) {
	clientRequestID = strings.TrimSpace(clientRequestID)
	if clientRequestID == "" {
		return nil, nil
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	h.pruneLocked(time.Now().UTC())
	return h.lookupIdempotentLocked(ownerID, clientRequestID, fingerprint)
}

func (h *TaskHub) SetCancel(taskID string, cancel context.CancelFunc) error {
	h.mu.Lock()
	defer h.mu.Unlock()
	stream, ok := h.tasks[normalizeTaskID(taskID)]
	if !ok {
		return ErrTaskNotFound
	}
	if stream.task.Status.Terminal() {
		return ErrTaskAlreadyFinished
	}
	if cancel == nil {
		return fmt.Errorf("%w: cancel function is required", ErrInvalidRequest)
	}
	stream.task.cancel = cancel
	return nil
}

func (h *TaskHub) CancelFunc(ownerID int64, taskID string) (context.CancelFunc, error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	stream, ok := h.tasks[normalizeTaskID(taskID)]
	if !ok || stream.task.ownerID != ownerID {
		return nil, ErrTaskNotFound
	}
	if stream.task.Status.Terminal() {
		return nil, ErrTaskAlreadyFinished
	}
	if stream.task.cancel == nil {
		return nil, ErrTaskNotReady
	}
	return stream.task.cancel, nil
}

// WorkerDone releases the per-thread execution guard only after the runtime
// goroutine has actually stopped. A cancelled run may already be terminal in
// the API while its Python stream is still unwinding.
func (h *TaskHub) WorkerDone(taskID string) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if stream, ok := h.tasks[normalizeTaskID(taskID)]; ok {
		stream.workerAlive = false
	}
}

// WorkerAlive exposes the monotonic local worker guard. It is used only after
// an authoritative terminal commit to decide whether the service must release
// the durable active-thread slot itself or the worker defer still owns that
// responsibility.
func (h *TaskHub) WorkerAlive(ownerID int64, taskID string) (bool, error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	stream, ok := h.tasks[normalizeTaskID(taskID)]
	if !ok || stream.task.ownerID != ownerID {
		return false, ErrTaskNotFound
	}
	return stream.workerAlive, nil
}

func (h *TaskHub) Get(ownerID int64, taskID string) (*Task, error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	stream, ok := h.tasks[taskID]
	if !ok || stream.task.ownerID != ownerID {
		return nil, ErrTaskNotFound
	}
	return cloneTask(&stream.task), nil
}

// List returns only the owner's tasks, optionally scoped to a thread, in
// reverse chronological order. Limits are normalized here as a final safety
// boundary even when callers have already validated query parameters.
func (h *TaskHub) List(ownerID int64, threadID string, limit int) ([]*Task, error) {
	if ownerID <= 0 {
		return nil, fmt.Errorf("%w: owner is required", ErrInvalidRequest)
	}
	if limit <= 0 {
		limit = defaultTaskListLimit
	}
	if limit > maxTaskListLimit {
		limit = maxTaskListLimit
	}
	threadID = strings.TrimSpace(threadID)

	h.mu.Lock()
	defer h.mu.Unlock()
	h.pruneLocked(time.Now().UTC())
	tasks := make([]*Task, 0, len(h.tasks))
	for _, stream := range h.tasks {
		if stream.task.ownerID != ownerID || (threadID != "" && stream.task.ThreadID != threadID) {
			continue
		}
		tasks = append(tasks, cloneTask(&stream.task))
	}
	sort.Slice(tasks, func(i, j int) bool {
		if tasks[i].CreatedAt.Equal(tasks[j].CreatedAt) {
			return tasks[i].RunID > tasks[j].RunID
		}
		return tasks[i].CreatedAt.After(tasks[j].CreatedAt)
	})
	if len(tasks) > limit {
		tasks = tasks[:limit]
	}
	return tasks, nil
}

func (h *TaskHub) Publish(taskID string, event AgentEvent) error {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.publishLocked(taskID, event)
}

func (h *TaskHub) publishLocked(taskID string, event AgentEvent) error {
	taskID = normalizeTaskID(taskID)
	stream, ok := h.tasks[taskID]
	if !ok {
		return ErrTaskNotFound
	}
	for _, previous := range stream.events {
		if event.EventID != "" && previous.EventID == event.EventID {
			return fmt.Errorf("%w: duplicate event id", ErrInvalidRequest)
		}
	}
	next, canonical, err := reduceTaskEvent(stream.task, event, time.Now().UTC())
	if err != nil {
		return err
	}
	h.commitLocked(stream, next, canonical)
	return nil
}

// PublishCommitted mirrors a transaction that has already committed to the
// durable repository. Subscribers are notified only after that commit.
func (h *TaskHub) PublishCommitted(task Task, event AgentEvent) error {
	h.mu.Lock()
	defer h.mu.Unlock()
	runID := normalizeTaskID(task.RunID)
	stream, ok := h.tasks[runID]
	if !ok {
		return ErrTaskNotFound
	}
	if event.Sequence == stream.task.LastEventSequence && event.EventID == stream.task.EventID {
		return nil
	}
	if task.ownerID != stream.task.ownerID || strings.TrimSpace(task.ThreadID) != stream.task.ThreadID ||
		event.Sequence != stream.task.LastEventSequence+1 || task.LastEventSequence != event.Sequence ||
		task.EventID != event.EventID || normalizeTaskID(event.RunID) != runID {
		return fmt.Errorf("%w: committed event does not extend the local run", ErrInvalidRequest)
	}
	for _, previous := range stream.events {
		if previous.EventID == event.EventID {
			return fmt.Errorf("%w: duplicate event id", ErrInvalidRequest)
		}
	}
	h.commitLocked(stream, task, event)
	return nil
}

func (h *TaskHub) commitLocked(stream *taskStream, task Task, event AgentEvent) {
	cancel := stream.task.cancel
	stream.events = append(stream.events, event)
	stream.task = task
	if !task.Status.Terminal() {
		stream.task.cancel = cancel
	}

	for subscriber := range stream.subscribers {
		select {
		case subscriber <- event:
		default:
			close(subscriber)
			delete(stream.subscribers, subscriber)
		}
	}
	if event.Terminal() {
		for subscriber := range stream.subscribers {
			close(subscriber)
			delete(stream.subscribers, subscriber)
		}
	}
}

func (h *TaskHub) Subscribe(ownerID int64, taskID string, after uint64) (*Subscription, error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	stream, ok := h.tasks[taskID]
	if !ok || stream.task.ownerID != ownerID {
		return nil, ErrTaskNotFound
	}
	if after > stream.task.LastEventSequence {
		return nil, fmt.Errorf("%w: after=%d last_event_sequence=%d", ErrEventCursorAhead, after, stream.task.LastEventSequence)
	}
	replay := make([]AgentEvent, 0, len(stream.events))
	for _, event := range stream.events {
		if event.Sequence > after {
			replay = append(replay, event)
		}
	}

	channel := make(chan AgentEvent, subscriberBuffer)
	if stream.task.Status.Terminal() {
		close(channel)
		return &Subscription{Replay: replay, Events: channel, Close: func() {}}, nil
	}
	stream.subscribers[channel] = struct{}{}
	var once sync.Once
	return &Subscription{
		Replay: replay,
		Events: channel,
		Close: func() {
			once.Do(func() {
				h.mu.Lock()
				defer h.mu.Unlock()
				current, exists := h.tasks[taskID]
				if !exists {
					return
				}
				if _, subscribed := current.subscribers[channel]; subscribed {
					delete(current.subscribers, channel)
					close(channel)
				}
			})
		},
	}, nil
}

func (h *TaskHub) Cancel(ownerID int64, taskID string) (context.CancelFunc, error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	stream, ok := h.tasks[taskID]
	if !ok || stream.task.ownerID != ownerID {
		return nil, ErrTaskNotFound
	}
	if stream.task.Status.Terminal() {
		return nil, ErrTaskAlreadyFinished
	}
	cancel := stream.task.cancel
	err := h.publishLocked(taskID, AgentEvent{
		TaskID:    taskID,
		Sequence:  stream.task.LastEventSequence + 1,
		CreatedAt: time.Now().UTC(),
		Type:      EventTaskFailed,
		Stage:     stream.task.Stage,
		Message:   "PPT task cancelled",
		Error: &ErrorDetail{
			Code:      "TASK_CANCELLED",
			Message:   "PPT task cancelled",
			Retryable: true,
		},
	})
	return cancel, err
}

func (h *TaskHub) pruneLocked(now time.Time) {
	for taskID, stream := range h.tasks {
		if stream.task.FinishedAt != nil && !stream.workerAlive && now.Sub(*stream.task.FinishedAt) >= h.ttl {
			h.deleteTaskLocked(taskID)
		}
	}
}

func (h *TaskHub) deleteTaskLocked(taskID string) {
	if stream, ok := h.tasks[taskID]; ok {
		for subscriber := range stream.subscribers {
			close(subscriber)
			delete(stream.subscribers, subscriber)
		}
		delete(h.tasks, taskID)
	}
	for key, record := range h.idempotency {
		if record.taskID == taskID {
			delete(h.idempotency, key)
		}
	}
}

func (h *TaskHub) lookupIdempotentLocked(ownerID int64, clientRequestID, fingerprint string) (*Task, error) {
	key := idempotencyKey{ownerID: ownerID, clientRequestID: clientRequestID}
	record, ok := h.idempotency[key]
	if !ok {
		return nil, nil
	}
	if record.fingerprint != fingerprint {
		return nil, fmt.Errorf("%w: client_request_id=%s", ErrClientRequestConflict, clientRequestID)
	}
	stream, ok := h.tasks[record.taskID]
	if !ok || stream.task.ownerID != ownerID {
		delete(h.idempotency, key)
		return nil, nil
	}
	return cloneTask(&stream.task), nil
}

func eventChannelForType(eventType EventType) EventChannel {
	switch eventType {
	case EventTaskStarted, EventTaskCompleted, EventTaskFailed:
		return EventChannelLifecycle
	case EventArtifactReady:
		return EventChannelArtifacts
	default:
		return EventChannelUpdates
	}
}

func cloneTask(task *Task) *Task {
	if task == nil {
		return nil
	}
	copy := *task
	copy.Result = cloneFinalResult(task.Result)
	copy.Error = cloneError(task.Error)
	copy.cancel = nil
	return &copy
}

func cloneFinalResult(result *FinalResult) *FinalResult {
	if result == nil {
		return nil
	}
	copy := *result
	return &copy
}

func cloneError(detail *ErrorDetail) *ErrorDetail {
	if detail == nil {
		return nil
	}
	copy := *detail
	return &copy
}
