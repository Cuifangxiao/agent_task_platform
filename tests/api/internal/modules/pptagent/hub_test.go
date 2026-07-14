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
