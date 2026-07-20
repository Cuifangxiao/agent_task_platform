package pptagent

import (
	"fmt"
	"strings"
	"time"
)

// reduceTaskEvent is the single state transition function shared by the
// in-process TaskHub and the durable MySQL event store. It canonicalizes an
// accepted runtime event and returns the next immutable task snapshot.
//
// Callers remain responsible for serializing the transition and for enforcing
// event_id uniqueness in their respective stores.
func reduceTaskEvent(task Task, event AgentEvent, receivedAt time.Time) (Task, AgentEvent, error) {
	if task.Status.Terminal() {
		return Task{}, AgentEvent{}, ErrTaskAlreadyFinished
	}
	if receivedAt.IsZero() {
		receivedAt = time.Now().UTC()
	} else {
		receivedAt = receivedAt.UTC()
	}

	runID := normalizeTaskID(task.RunID)
	if runID == "" {
		runID = normalizeTaskID(task.TaskID)
	}
	threadID := strings.TrimSpace(task.ThreadID)
	if runID == "" || threadID == "" || task.ownerID <= 0 {
		return Task{}, AgentEvent{}, fmt.Errorf("%w: task snapshot is incomplete", ErrInvalidRequest)
	}

	eventTaskID := normalizeTaskID(event.TaskID)
	eventRunID := normalizeTaskID(event.RunID)
	if eventTaskID != "" && eventRunID != "" && eventTaskID != eventRunID {
		return Task{}, AgentEvent{}, fmt.Errorf("%w: event task_id and run_id do not match", ErrInvalidRequest)
	}
	if eventTaskID != "" && eventTaskID != runID {
		return Task{}, AgentEvent{}, fmt.Errorf("%w: event task id does not match", ErrInvalidRequest)
	}
	if eventRunID != "" && eventRunID != runID {
		return Task{}, AgentEvent{}, fmt.Errorf("%w: event run id does not match", ErrInvalidRequest)
	}
	if event.ThreadID != "" && strings.TrimSpace(event.ThreadID) != threadID {
		return Task{}, AgentEvent{}, fmt.Errorf("%w: event thread id does not match", ErrInvalidRequest)
	}

	event.SchemaVersion = CurrentSchemaVersion
	event.ThreadID = threadID
	event.RunID = runID
	event.TaskID = runID
	event.Channel = eventChannelForType(event.Type)
	if event.EventID == "" {
		var err error
		event.EventID, err = randomUUID()
		if err != nil {
			return Task{}, AgentEvent{}, fmt.Errorf("create event id: %w", err)
		}
	}
	if event.CreatedAt.IsZero() {
		event.CreatedAt = receivedAt
	} else {
		event.CreatedAt = event.CreatedAt.UTC()
	}
	if err := validateAgentEvent(event); err != nil {
		return Task{}, AgentEvent{}, fmt.Errorf("%w: %v", ErrInvalidRequest, err)
	}

	expected := task.LastEventSequence + 1
	if event.Sequence != expected {
		return Task{}, AgentEvent{}, fmt.Errorf("%w: event sequence %d, expected %d", ErrInvalidRequest, event.Sequence, expected)
	}
	if task.LastEventSequence == 0 && event.Type != EventTaskStarted && event.Type != EventTaskFailed {
		return Task{}, AgentEvent{}, fmt.Errorf("%w: first event must start or fail the task", ErrInvalidRequest)
	}
	if task.LastEventSequence > 0 && event.Type == EventTaskStarted {
		return Task{}, AgentEvent{}, fmt.Errorf("%w: task started event cannot repeat", ErrInvalidRequest)
	}

	next := task
	next.SchemaVersion = event.SchemaVersion
	next.EventID = event.EventID
	next.ThreadID = event.ThreadID
	next.RunID = event.RunID
	next.TaskID = event.RunID
	next.Stage = event.Stage
	next.Message = event.Message
	next.LastEventSequence = event.Sequence
	next.UpdatedAt = receivedAt

	switch event.Type {
	case EventTaskCompleted:
		next.Status = TaskSucceeded
		next.Result = cloneFinalResult(event.FinalResult)
		next.Error = nil
		if event.FinalResult != nil {
			next.FileURL = event.FinalResult.PPTFile.URL
		}
		finishedAt := receivedAt
		next.FinishedAt = &finishedAt
	case EventTaskFailed:
		next.Status = TaskFailed
		if event.Error != nil && event.Error.Code == "TASK_CANCELLED" {
			next.Status = TaskCancelled
		}
		next.Result = nil
		next.FileURL = ""
		next.Error = cloneError(event.Error)
		finishedAt := receivedAt
		next.FinishedAt = &finishedAt
	default:
		next.Status = TaskRunning
	}

	return next, event, nil
}
