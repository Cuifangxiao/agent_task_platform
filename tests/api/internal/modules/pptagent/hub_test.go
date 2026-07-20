package pptagent

import (
	"context"
	"errors"
	"testing"
	"time"
)

func TestTaskHubReplaysEventsAndClosesAfterTerminalEvent(t *testing.T) {
	hub := NewTaskHub(time.Hour)
	taskID := "33333333-3333-4333-8333-333333333333"
	if _, err := hub.Create(taskID, 7, time.Now().UTC()); err != nil {
		t.Fatal(err)
	}
	first := AgentEvent{TaskID: taskID, Sequence: 1, Type: EventTaskStarted, Stage: StagePreparing, Message: "started"}
	if err := hub.Publish(taskID, first); err != nil {
		t.Fatal(err)
	}
	subscription, err := hub.Subscribe(7, taskID, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer subscription.Close()
	if len(subscription.Replay) != 1 || subscription.Replay[0].Sequence != 1 {
		t.Fatalf("unexpected replay: %#v", subscription.Replay)
	}

	completed := AgentEvent{
		TaskID: taskID, Sequence: 2, Type: EventTaskCompleted, Stage: StageFinished, Message: "done",
		FinalResult: &FinalResult{PPTFile: ResourceRef{
			ResourceID: "pptx", Type: ResourcePPTX, Name: "deck.pptx", RelativePath: "exports/deck.pptx",
		}},
	}
	if err := hub.Publish(taskID, completed); err != nil {
		t.Fatal(err)
	}
	select {
	case event := <-subscription.Events:
		if event.Type != EventTaskCompleted {
			t.Fatalf("event type = %s", event.Type)
		}
	case <-time.After(time.Second):
		t.Fatal("terminal event was not delivered")
	}
	if _, open := <-subscription.Events; open {
		t.Fatal("subscription remained open after terminal event")
	}
	task, _ := hub.Get(7, taskID)
	if task.Status != TaskSucceeded || task.LastEventSequence != 2 {
		t.Fatalf("unexpected task: %#v", task)
	}
}

func TestTaskHubRejectsWrongOwnerAndOutOfOrderSequence(t *testing.T) {
	hub := NewTaskHub(time.Hour)
	taskID := "44444444-4444-4444-8444-444444444444"
	_, _ = hub.Create(taskID, 7, time.Now().UTC())
	if _, err := hub.Get(8, taskID); !errors.Is(err, ErrTaskNotFound) {
		t.Fatalf("wrong owner error = %v", err)
	}
	if err := hub.Publish(taskID, AgentEvent{TaskID: taskID, Sequence: 2, Type: EventStepProgress}); !errors.Is(err, ErrInvalidRequest) {
		t.Fatalf("out-of-order error = %v", err)
	}
}

func TestTaskHubCancelCreatesSingleTerminalEvent(t *testing.T) {
	hub := NewTaskHub(time.Hour)
	taskID := "55555555-5555-4555-8555-555555555555"
	_, _ = hub.Create(taskID, 7, time.Now().UTC())
	cancelled := false
	_ = hub.SetCancel(taskID, func() { cancelled = true })
	cancel, err := hub.Cancel(7, taskID)
	if err != nil {
		t.Fatal(err)
	}
	cancel()
	if !cancelled {
		t.Fatal("task context cancel function was not returned")
	}
	task, _ := hub.Get(7, taskID)
	if task.Status != TaskCancelled || task.Error == nil || task.Error.Code != "TASK_CANCELLED" {
		t.Fatalf("unexpected cancelled task: %#v", task)
	}
	if err := hub.Publish(taskID, AgentEvent{TaskID: taskID, Sequence: 2, Type: EventTaskFailed}); !errors.Is(err, ErrTaskAlreadyFinished) {
		t.Fatalf("second terminal error = %v", err)
	}

	subscription, err := hub.Subscribe(7, taskID, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer subscription.Close()
	if len(subscription.Replay) != 1 || subscription.Replay[0].Error == nil || subscription.Replay[0].Error.Code != "TASK_CANCELLED" {
		t.Fatalf("unexpected cancellation replay: %#v", subscription.Replay)
	}
}

func TestSubscriptionCloseIsIdempotent(t *testing.T) {
	hub := NewTaskHub(time.Hour)
	taskID := "66666666-6666-4666-8666-666666666666"
	_, _ = hub.Create(taskID, 7, time.Now().UTC())
	subscription, _ := hub.Subscribe(7, taskID, 0)
	subscription.Close()
	subscription.Close()
	select {
	case _, open := <-subscription.Events:
		if open {
			t.Fatal("closed subscription channel is still open")
		}
	case <-context.Background().Done():
	}
}

func TestTaskHubInvalidateClosesSubscribersAndDeletesHotState(t *testing.T) {
	hub := NewTaskHub(time.Hour)
	if _, _, err := hub.CreateRun("run-invalidate", "thread-invalidate", "request-invalidate", "fingerprint-invalidate", 7, time.Now().UTC()); err != nil {
		t.Fatal(err)
	}
	subscription, err := hub.Subscribe(7, "run-invalidate", 0)
	if err != nil {
		t.Fatal(err)
	}
	cancelled := false
	if err := hub.SetCancel("run-invalidate", func() { cancelled = true }); err != nil {
		t.Fatal(err)
	}

	hub.Invalidate(" run-invalidate ")
	if !cancelled {
		t.Fatal("invalidating a running hot stream did not stop its worker")
	}
	assertSubscriptionClosed(t, subscription.Events)
	subscription.Close()
	subscription.Close()

	if _, err := hub.Get(7, "run-invalidate"); !errors.Is(err, ErrTaskNotFound) {
		t.Fatalf("invalidated task lookup error = %v", err)
	}
	existing, err := hub.FindIdempotent(7, "request-invalidate", "fingerprint-invalidate")
	if err != nil {
		t.Fatal(err)
	}
	if existing != nil {
		t.Fatalf("invalidated idempotency record still returned task: %#v", existing)
	}
}

func TestTaskHubPruneClosesSubscribersAndDeletesIdempotency(t *testing.T) {
	hub := NewTaskHub(time.Hour)
	if _, _, err := hub.CreateRun("run-expired", "thread-expired", "request-expired", "fingerprint-expired", 7, time.Now().UTC()); err != nil {
		t.Fatal(err)
	}
	subscription, err := hub.Subscribe(7, "run-expired", 0)
	if err != nil {
		t.Fatal(err)
	}

	finishedAt := time.Now().UTC().Add(-2 * time.Hour)
	hub.mu.Lock()
	hub.tasks["run-expired"].task.FinishedAt = &finishedAt
	hub.tasks["run-expired"].workerAlive = false
	hub.mu.Unlock()

	existing, err := hub.FindIdempotent(7, "request-expired", "fingerprint-expired")
	if err != nil {
		t.Fatal(err)
	}
	if existing != nil {
		t.Fatalf("pruned idempotency record still returned task: %#v", existing)
	}
	assertSubscriptionClosed(t, subscription.Events)
	subscription.Close()
	if _, err := hub.Get(7, "run-expired"); !errors.Is(err, ErrTaskNotFound) {
		t.Fatalf("pruned task lookup error = %v", err)
	}
}

func TestTaskHubCancelFunctionReadinessAndTerminalBoundaries(t *testing.T) {
	hub := NewTaskHub(time.Hour)
	now := time.Now().UTC()
	if err := hub.Register(Task{
		SchemaVersion: CurrentSchemaVersion,
		RunID:         "run-cancel-boundary",
		TaskID:        "run-cancel-boundary",
		ThreadID:      "thread-cancel-boundary",
		Status:        TaskRunning,
		Stage:         StagePreparing,
		CreatedAt:     now,
		UpdatedAt:     now,
		ownerID:       7,
	}, ""); err != nil {
		t.Fatal(err)
	}

	if _, err := hub.CancelFunc(7, " run-cancel-boundary "); !errors.Is(err, ErrTaskNotReady) {
		t.Fatalf("cancel before worker initialization error = %v", err)
	}
	if err := hub.SetCancel("run-cancel-boundary", nil); !errors.Is(err, ErrInvalidRequest) {
		t.Fatalf("nil cancel function error = %v", err)
	}
	cancelled := false
	if err := hub.SetCancel(" run-cancel-boundary ", func() { cancelled = true }); err != nil {
		t.Fatal(err)
	}
	cancel, err := hub.CancelFunc(7, "run-cancel-boundary")
	if err != nil {
		t.Fatal(err)
	}
	cancel()
	if !cancelled {
		t.Fatal("installed cancel function was not returned")
	}
	if alive, err := hub.WorkerAlive(7, "run-cancel-boundary"); err != nil || !alive {
		t.Fatalf("worker alive before completion = %v, err %v", alive, err)
	}
	hub.WorkerDone("run-cancel-boundary")
	if alive, err := hub.WorkerAlive(7, "run-cancel-boundary"); err != nil || alive {
		t.Fatalf("worker alive after WorkerDone = %v, err %v", alive, err)
	}

	if err := hub.Publish("run-cancel-boundary", AgentEvent{
		Sequence: 1,
		Type:     EventTaskFailed,
		Stage:    StagePreparing,
		Message:  "failed",
		Error:    &ErrorDetail{Code: "FAILED", Message: "failed"},
	}); err != nil {
		t.Fatal(err)
	}
	if err := hub.SetCancel("run-cancel-boundary", func() {}); !errors.Is(err, ErrTaskAlreadyFinished) {
		t.Fatalf("set cancel after terminal error = %v", err)
	}
	if _, err := hub.CancelFunc(7, "run-cancel-boundary"); !errors.Is(err, ErrTaskAlreadyFinished) {
		t.Fatalf("get cancel after terminal error = %v", err)
	}
}

func assertSubscriptionClosed(t *testing.T, events <-chan AgentEvent) {
	t.Helper()
	select {
	case _, open := <-events:
		if open {
			t.Fatal("subscription channel remained open")
		}
	case <-time.After(time.Second):
		t.Fatal("subscription channel was not closed")
	}
}

func TestTaskHubFillsThreadRunAndChannelEnvelope(t *testing.T) {
	hub := NewTaskHub(time.Hour)
	task, existing, err := hub.CreateRunWithDetails(
		"run-1", "thread-1", "request-1", "fingerprint-1", "Architecture deck", 42, 7, time.Now().UTC(),
	)
	if err != nil || existing {
		t.Fatalf("CreateRunWithDetails returned task=%#v existing=%v err=%v", task, existing, err)
	}
	if task.SchemaVersion != CurrentSchemaVersion || task.EventID != "" || task.RunID != "run-1" || task.TaskID != task.RunID || task.ThreadID != "thread-1" {
		t.Fatalf("unexpected creation envelope: %#v", task)
	}
	if task.Description != "Architecture deck" || task.AgentConfigID != 42 {
		t.Fatalf("task request metadata was not retained: %#v", task)
	}

	events := []AgentEvent{
		{Sequence: 1, Type: EventTaskStarted, Stage: StagePreparing, Message: "started"},
		{Sequence: 2, Type: EventStepProgress, Stage: StagePlanning, Message: "planning"},
		{
			Sequence: 3,
			Type:     EventArtifactReady,
			Stage:    StageRendering,
			Message:  "preview",
			Resources: []ResourceRef{{
				ResourceID: "preview-1", Type: ResourcePagePreview, Name: "preview.png", RelativePath: "preview.png",
			}},
		},
	}
	for _, event := range events {
		if err := hub.Publish(task.RunID, event); err != nil {
			t.Fatal(err)
		}
	}
	subscription, err := hub.Subscribe(7, task.RunID, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer subscription.Close()
	if len(subscription.Replay) != 3 {
		t.Fatalf("replay length = %d, want 3", len(subscription.Replay))
	}
	wantChannels := []EventChannel{EventChannelLifecycle, EventChannelUpdates, EventChannelArtifacts}
	seenIDs := make(map[string]struct{}, len(subscription.Replay))
	for index, event := range subscription.Replay {
		if event.SchemaVersion != CurrentSchemaVersion || event.EventID == "" || event.ThreadID != task.ThreadID || event.RunID != task.RunID || event.TaskID != task.RunID {
			t.Fatalf("event %d has incomplete envelope: %#v", index, event)
		}
		if event.Channel != wantChannels[index] {
			t.Fatalf("event %d channel = %q, want %q", index, event.Channel, wantChannels[index])
		}
		if _, duplicate := seenIDs[event.EventID]; duplicate {
			t.Fatalf("duplicate event id %q", event.EventID)
		}
		seenIDs[event.EventID] = struct{}{}
	}
	latest, err := hub.Get(7, task.RunID)
	if err != nil {
		t.Fatal(err)
	}
	if latest.EventID != subscription.Replay[2].EventID {
		t.Fatalf("task event id = %q, want latest event %q", latest.EventID, subscription.Replay[2].EventID)
	}
}

func TestTaskHubCreateRunIsIdempotentPerOwner(t *testing.T) {
	hub := NewTaskHub(time.Hour)
	created, existing, err := hub.CreateRun("run-1", "thread-1", "request-1", "fingerprint-1", 7, time.Now().UTC())
	if err != nil || existing {
		t.Fatalf("first create returned task=%#v existing=%v err=%v", created, existing, err)
	}
	replayed, existing, err := hub.CreateRun("run-2", "thread-2", "request-1", "fingerprint-1", 7, time.Now().UTC())
	if err != nil || !existing {
		t.Fatalf("idempotent create returned task=%#v existing=%v err=%v", replayed, existing, err)
	}
	if replayed.RunID != created.RunID || replayed.ThreadID != created.ThreadID {
		t.Fatalf("idempotent create returned a different task: created=%#v replayed=%#v", created, replayed)
	}
	if _, _, err := hub.CreateRun("run-3", "thread-3", "request-1", "different-fingerprint", 7, time.Now().UTC()); !errors.Is(err, ErrClientRequestConflict) {
		t.Fatalf("different payload error = %v", err)
	}
	if _, _, err := hub.CreateRun("run-4", "thread-4", "request-1", "different-fingerprint", 8, time.Now().UTC()); err != nil {
		t.Fatalf("idempotency key unexpectedly leaked across owners: %v", err)
	}
}

func TestTaskHubAllowsOnlyOneActiveRunPerOwnerThread(t *testing.T) {
	hub := NewTaskHub(time.Hour)
	createdAt := time.Now().UTC()
	if _, _, err := hub.CreateRun("run-1", "thread-1", "", "", 7, createdAt); err != nil {
		t.Fatal(err)
	}
	if _, _, err := hub.CreateRun("run-2", "thread-1", "", "", 7, createdAt.Add(time.Second)); !errors.Is(err, ErrThreadRunConflict) {
		t.Fatalf("same-thread active run error = %v", err)
	}
	if _, _, err := hub.CreateRun("run-3", "thread-2", "", "", 7, createdAt.Add(2*time.Second)); err != nil {
		t.Fatalf("different thread was blocked: %v", err)
	}
	if _, _, err := hub.CreateRun("run-4", "thread-1", "", "", 8, createdAt.Add(3*time.Second)); err != nil {
		t.Fatalf("different owner was blocked: %v", err)
	}
	if err := hub.Publish("run-1", AgentEvent{
		Sequence: 1,
		Type:     EventTaskFailed,
		Stage:    StagePreparing,
		Message:  "failed",
		Error:    &ErrorDetail{Code: "FAILED", Message: "failed"},
	}); err != nil {
		t.Fatal(err)
	}
	if _, _, err := hub.CreateRun("run-5", "thread-1", "", "", 7, createdAt.Add(4*time.Second)); !errors.Is(err, ErrThreadRunConflict) {
		t.Fatalf("terminal state released thread before worker exit: %v", err)
	}
	hub.WorkerDone("run-1")
	if _, _, err := hub.CreateRun("run-5", "thread-1", "", "", 7, createdAt.Add(4*time.Second)); err != nil {
		t.Fatalf("stopped worker did not release thread: %v", err)
	}
}

func TestTaskHubRejectsCursorAheadOfWatermark(t *testing.T) {
	hub := NewTaskHub(time.Hour)
	if _, err := hub.Create("run-1", 7, time.Now().UTC()); err != nil {
		t.Fatal(err)
	}
	if err := hub.Publish("run-1", AgentEvent{Sequence: 1, Type: EventTaskStarted, Stage: StagePreparing, Message: "started"}); err != nil {
		t.Fatal(err)
	}
	if _, err := hub.Subscribe(7, "run-1", 2); !errors.Is(err, ErrEventCursorAhead) {
		t.Fatalf("cursor-ahead subscribe error = %v", err)
	}
	if subscription, err := hub.Subscribe(7, "run-1", 1); err != nil {
		t.Fatalf("watermark cursor was rejected: %v", err)
	} else {
		subscription.Close()
	}
}

func TestTaskHubListIsOwnerSafeFilteredAndReverseChronological(t *testing.T) {
	hub := NewTaskHub(time.Hour)
	createdAt := time.Now().UTC().Add(-time.Minute)
	if _, _, err := hub.CreateRun("run-old", "thread-1", "", "", 7, createdAt); err != nil {
		t.Fatal(err)
	}
	if err := hub.Publish("run-old", AgentEvent{
		Sequence: 1,
		Type:     EventTaskFailed,
		Stage:    StagePreparing,
		Message:  "failed",
		Error:    &ErrorDetail{Code: "FAILED", Message: "failed"},
	}); err != nil {
		t.Fatal(err)
	}
	hub.WorkerDone("run-old")
	if _, _, err := hub.CreateRun("run-new", "thread-1", "", "", 7, createdAt.Add(time.Second)); err != nil {
		t.Fatal(err)
	}
	if _, _, err := hub.CreateRun("run-other-thread", "thread-2", "", "", 7, createdAt.Add(2*time.Second)); err != nil {
		t.Fatal(err)
	}
	if _, _, err := hub.CreateRun("run-other-owner", "thread-3", "", "", 8, createdAt.Add(3*time.Second)); err != nil {
		t.Fatal(err)
	}

	tasks, err := hub.List(7, "thread-1", 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(tasks) != 2 || tasks[0].RunID != "run-new" || tasks[1].RunID != "run-old" {
		t.Fatalf("unexpected filtered order: %#v", tasks)
	}
	limited, err := hub.List(7, "thread-1", 1)
	if err != nil {
		t.Fatal(err)
	}
	if len(limited) != 1 || limited[0].RunID != "run-new" {
		t.Fatalf("unexpected limited result: %#v", limited)
	}
	allOwnerTasks, err := hub.List(7, "", 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(allOwnerTasks) != 3 || allOwnerTasks[0].RunID != "run-other-thread" {
		t.Fatalf("unexpected owner list: %#v", allOwnerTasks)
	}
}
