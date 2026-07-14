package pptagent

import (
	"context"
	"fmt"
	"sync"
	"time"
)

const subscriberBuffer = 64

type taskStream struct {
	task        Task
	events      []AgentEvent
	subscribers map[chan AgentEvent]struct{}
}

// TaskHub is the only task state store used by the MVP pipeline. It keeps a
// short replay buffer so a browser can connect after POST /tasks returns.
type TaskHub struct {
	mu    sync.Mutex
	tasks map[string]*taskStream
	ttl   time.Duration
}

func NewTaskHub(ttl time.Duration) *TaskHub {
	if ttl <= 0 {
		ttl = time.Hour
	}
	return &TaskHub{tasks: make(map[string]*taskStream), ttl: ttl}
}

func (h *TaskHub) Create(taskID string, ownerID int64, now time.Time) (*Task, error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.pruneLocked(now)
	if taskID == "" || ownerID <= 0 {
		return nil, fmt.Errorf("%w: task id and owner are required", ErrInvalidRequest)
	}
	if _, exists := h.tasks[taskID]; exists {
		return nil, fmt.Errorf("%w: duplicate task id", ErrInvalidRequest)
	}
	stream := &taskStream{
		task: Task{
			TaskID:    taskID,
			Status:    TaskRunning,
			Stage:     StagePreparing,
			Message:   "PPT task created",
			EventsURL: "/api/v1/ppt/tasks/" + taskID + "/events",
			CreatedAt: now,
			UpdatedAt: now,
			ownerID:   ownerID,
		},
		subscribers: make(map[chan AgentEvent]struct{}),
	}
	h.tasks[taskID] = stream
	return cloneTask(&stream.task), nil
}

func (h *TaskHub) SetCancel(taskID string, cancel context.CancelFunc) error {
	h.mu.Lock()
	defer h.mu.Unlock()
	stream, ok := h.tasks[taskID]
	if !ok {
		return ErrTaskNotFound
	}
	stream.task.cancel = cancel
	return nil
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

func (h *TaskHub) Publish(taskID string, event AgentEvent) error {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.publishLocked(taskID, event)
}

func (h *TaskHub) publishLocked(taskID string, event AgentEvent) error {
	stream, ok := h.tasks[taskID]
	if !ok {
		return ErrTaskNotFound
	}
	if stream.task.Status.Terminal() {
		return ErrTaskAlreadyFinished
	}
	if event.TaskID == "" {
		event.TaskID = taskID
	}
	if event.TaskID != taskID {
		return fmt.Errorf("%w: event task id does not match", ErrInvalidRequest)
	}
	if err := validateAgentEvent(event); err != nil {
		return fmt.Errorf("%w: %v", ErrInvalidRequest, err)
	}
	if len(stream.events) == 0 && event.Type != EventTaskStarted && event.Type != EventTaskFailed {
		return fmt.Errorf("%w: first event must start or fail the task", ErrInvalidRequest)
	}
	if len(stream.events) > 0 && event.Type == EventTaskStarted {
		return fmt.Errorf("%w: task started event cannot repeat", ErrInvalidRequest)
	}
	expected := stream.task.LastEventSequence + 1
	if event.Sequence != expected {
		return fmt.Errorf("%w: event sequence %d, expected %d", ErrInvalidRequest, event.Sequence, expected)
	}
	receivedAt := time.Now().UTC()
	if event.CreatedAt.IsZero() {
		event.CreatedAt = receivedAt
	}

	stream.events = append(stream.events, event)
	stream.task.Stage = event.Stage
	stream.task.Message = event.Message
	stream.task.LastEventSequence = event.Sequence
	stream.task.UpdatedAt = receivedAt
	switch event.Type {
	case EventTaskCompleted:
		stream.task.Status = TaskSucceeded
		stream.task.Result = cloneFinalResult(event.FinalResult)
		if event.FinalResult != nil {
			stream.task.FileURL = event.FinalResult.PPTFile.URL
		}
		h.finishLocked(stream, receivedAt)
	case EventTaskFailed:
		stream.task.Status = TaskFailed
		if event.Error != nil && event.Error.Code == "TASK_CANCELLED" {
			stream.task.Status = TaskCancelled
		}
		stream.task.Error = cloneError(event.Error)
		h.finishLocked(stream, receivedAt)
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
	return nil
}

func (h *TaskHub) Subscribe(ownerID int64, taskID string, after uint64) (*Subscription, error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	stream, ok := h.tasks[taskID]
	if !ok || stream.task.ownerID != ownerID {
		return nil, ErrTaskNotFound
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

func (h *TaskHub) finishLocked(stream *taskStream, finishedAt time.Time) {
	stream.task.FinishedAt = &finishedAt
	stream.task.cancel = nil
}

func (h *TaskHub) pruneLocked(now time.Time) {
	for taskID, stream := range h.tasks {
		if stream.task.FinishedAt != nil && now.Sub(*stream.task.FinishedAt) >= h.ttl {
			delete(h.tasks, taskID)
		}
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
