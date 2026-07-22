package pptagent

import (
	"context"
	"errors"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"

	"agent-task-platform/apps/api/internal/shared/actor"
)

func TestPersistentServiceReadsAndReplaysDBOnlyTerminalRun(t *testing.T) {
	runID := "11111111-1111-4111-8111-111111111111"
	finishedAt := time.Now().UTC().Truncate(time.Second)
	task := persistentTestTask(runID, 7, TaskSucceeded)
	task.LastEventSequence = 3
	task.EventID = "event-3"
	task.Stage = StageFinished
	task.Message = "done"
	task.Result = &FinalResult{
		PPTFile: ResourceRef{
			ResourceID:   "pptx",
			Type:         ResourcePPTX,
			Name:         "deck.pptx",
			RelativePath: "exports/deck.pptx",
		},
		SlideCount: 3,
	}
	task.FinishedAt = &finishedAt
	task.UpdatedAt = finishedAt

	events := []AgentEvent{
		persistentTestEvent(task, 1, "event-1", EventTaskStarted, StagePreparing, "started"),
		persistentTestEvent(task, 2, "event-2", EventStepProgress, StagePlanning, "planning"),
		persistentTestEvent(task, 3, "event-3", EventTaskCompleted, StageFinished, "done"),
	}
	events[2].FinalResult = cloneFinalResult(task.Result)
	repo := newMemoryRunRepository(task)
	repo.events[runID] = append([]AgentEvent(nil), events...)
	service := NewPersistentService(nil, nil, nil, repo, t.TempDir(), NewTaskHub(time.Hour))

	got, err := service.GetTask(t.Context(), 7, runID)
	if err != nil {
		t.Fatal(err)
	}
	if got.RunID != runID || got.Status != TaskSucceeded || got.LastEventSequence != 3 {
		t.Fatalf("unexpected durable task: %#v", got)
	}
	listed, err := service.ListTasks(t.Context(), 7, "thread-"+runID, 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(listed) != 1 || listed[0].RunID != runID {
		t.Fatalf("unexpected durable task list: %#v", listed)
	}

	subscription, err := service.SubscribeTask(t.Context(), 7, runID, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer subscription.Close()
	if len(subscription.Replay) != 3 {
		t.Fatalf("replay length = %d, want 3", len(subscription.Replay))
	}
	for index, event := range subscription.Replay {
		wantSequence := uint64(index + 1)
		if event.Sequence != wantSequence || event.RunID != runID || event.TaskID != runID {
			t.Fatalf("replay[%d] = %#v, want sequence %d for run %s", index, event, wantSequence, runID)
		}
	}
	if _, open := <-subscription.Events; open {
		t.Fatal("DB-only terminal subscription live channel remained open")
	}
}

func TestPersistentServiceOpensArtifactForDBOnlySucceededRun(t *testing.T) {
	runID := "22222222-2222-4222-8222-222222222222"
	artifactDir := t.TempDir()
	relativePath := filepath.Join("exports", "deck.pptx")
	taskDir := filepath.Join(artifactDir, runID, "exports")
	if err := os.MkdirAll(taskDir, 0o755); err != nil {
		t.Fatal(err)
	}
	want := []byte("durable deck bytes")
	if err := os.WriteFile(filepath.Join(artifactDir, runID, relativePath), want, 0o600); err != nil {
		t.Fatal(err)
	}

	task := persistentTestTask(runID, 7, TaskSucceeded)
	task.Stage = StageFinished
	task.Result = &FinalResult{PPTFile: ResourceRef{
		ResourceID:   "pptx",
		Type:         ResourcePPTX,
		Name:         "deck.pptx",
		RelativePath: filepath.ToSlash(relativePath),
	}}
	task.FileURL = "/api/v1/ppt/tasks/" + runID + "/file"
	finishedAt := time.Now().UTC()
	task.FinishedAt = &finishedAt

	repo := newMemoryRunRepository(task)
	service := NewPersistentService(nil, nil, nil, repo, artifactDir, NewTaskHub(time.Hour))
	file, err := service.OpenArtifact(t.Context(), 7, runID, "")
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	got, err := io.ReadAll(file)
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != string(want) {
		t.Fatalf("artifact content = %q, want %q", got, want)
	}
}

func TestPersistentServiceCreateOrGetExistingDoesNotStartSecondWorker(t *testing.T) {
	runID := "33333333-3333-4333-8333-333333333333"
	existing := persistentTestTask(runID, 7, TaskRunning)
	existing.ThreadID = "thread-existing"
	existing.ClientRequestID = "request-existing"
	existing.Description = "Build an architecture deck"

	repo := newMemoryRunRepository(existing)
	repo.forceIdempotentMiss = true
	repo.createResult = cloneTask(&existing)
	repo.createCreated = false
	runtimeCalls := make(chan struct{}, 1)
	runtime := &persistenceCountingRuntime{calls: runtimeCalls}
	hub := NewTaskHub(time.Hour)
	service := NewPersistentService(
		&stubModelRepo{config: activeModelConfig()},
		&stubAgentRepo{config: activeAgentConfig()},
		runtime,
		repo,
		t.TempDir(),
		hub,
	)

	created, err := service.CreateTask(t.Context(), actor.User{UserID: 7}, CreateTaskRequest{
		ThreadID:        existing.ThreadID,
		ClientRequestID: existing.ClientRequestID,
		Description:     existing.Description,
	})
	if err != nil {
		t.Fatal(err)
	}
	if created.RunID != runID {
		t.Fatalf("CreateOrGet returned run %q, want existing run %q", created.RunID, runID)
	}
	if repo.createCalls != 1 {
		t.Fatalf("CreateOrGet calls = %d, want 1", repo.createCalls)
	}
	select {
	case <-runtimeCalls:
		t.Fatal("created=false started a second runtime worker")
	case <-time.After(50 * time.Millisecond):
	}
	if _, err := hub.Get(7, runID); !errors.Is(err, ErrTaskNotFound) {
		t.Fatalf("created=false unexpectedly registered another local worker: %v", err)
	}
}

func TestPersistentServiceRecoversCreateCommittedBeforeConnectionError(t *testing.T) {
	repo := newMemoryRunRepository()
	repo.createErr = errors.New("connection lost after insert commit")
	repo.storeCreateOnError = true
	runtimeCalls := make(chan struct{}, 1)
	service := NewPersistentService(
		&stubModelRepo{config: activeModelConfig()},
		&stubAgentRepo{config: activeAgentConfig()},
		&persistenceCountingRuntime{calls: runtimeCalls},
		repo,
		t.TempDir(),
		NewTaskHub(time.Hour),
	)

	task, err := service.CreateTask(t.Context(), actor.User{UserID: 7}, CreateTaskRequest{
		ThreadID: "ambiguous-create-thread", Description: "Build a durable deck",
	})
	if err != nil {
		t.Fatalf("ambiguous committed create was not recovered: %v", err)
	}
	if task == nil || task.RunID == "" || task.AgentConfigID != activeAgentConfig().ID {
		t.Fatalf("unexpected recovered task: %#v", task)
	}
	select {
	case <-runtimeCalls:
	case <-time.After(time.Second):
		t.Fatal("recovered durable create did not start its worker")
	}
}

func TestPersistentServiceCancellationWaitsForWorkerInitialization(t *testing.T) {
	createRelease := make(chan struct{})
	createObserved := make(chan Task, 1)
	repo := newMemoryRunRepository()
	repo.createObserved = createObserved
	repo.createRelease = createRelease
	runtime := &gatedRuntime{contexts: make(chan context.Context, 1)}
	service := NewPersistentService(
		&stubModelRepo{config: activeModelConfig()},
		&stubAgentRepo{config: activeAgentConfig()},
		runtime,
		repo,
		t.TempDir(),
		NewTaskHub(time.Hour),
	)

	type createResult struct {
		task *Task
		err  error
	}
	created := make(chan createResult, 1)
	go func() {
		task, err := service.CreateTask(context.Background(), actor.User{UserID: 7}, CreateTaskRequest{
			ThreadID: "initializing-thread", Description: "Build a deck",
		})
		created <- createResult{task: task, err: err}
	}()
	seed := <-createObserved
	cancelled := make(chan createResult, 1)
	go func() {
		task, err := service.CancelTask(context.Background(), 7, seed.RunID)
		cancelled <- createResult{task: task, err: err}
	}()
	select {
	case result := <-cancelled:
		t.Fatalf("cancel escaped the initialization transition lock: %#v", result)
	case <-time.After(25 * time.Millisecond):
	}
	close(createRelease)
	createResultValue := <-created
	if createResultValue.err != nil {
		t.Fatal(createResultValue.err)
	}
	cancelResult := <-cancelled
	if cancelResult.err != nil || cancelResult.task == nil || cancelResult.task.Status != TaskCancelled {
		t.Fatalf("cancel after initialization = task %#v, err %v", cancelResult.task, cancelResult.err)
	}
}

func TestPersistentServiceReconcilesAmbiguousCancellationCommit(t *testing.T) {
	runID := "55555555-5555-4555-8555-555555555555"
	task := persistentTestTask(runID, 7, TaskRunning)
	repo := newMemoryRunRepository(task)
	repo.appendCommitErr = errors.New("connection lost after cancellation commit")
	hub := NewTaskHub(time.Hour)
	if err := hub.Register(task, ""); err != nil {
		t.Fatal(err)
	}
	cancelledWorker := false
	if err := hub.SetCancel(runID, func() { cancelledWorker = true }); err != nil {
		t.Fatal(err)
	}
	service := NewPersistentService(nil, nil, nil, repo, t.TempDir(), hub)

	cancelled, err := service.CancelTask(t.Context(), 7, runID)
	if err != nil {
		t.Fatalf("durably committed cancellation returned error: %v", err)
	}
	if cancelled.Status != TaskCancelled || cancelled.Error == nil || cancelled.Error.Code != "TASK_CANCELLED" {
		t.Fatalf("unexpected reconciled cancellation: %#v", cancelled)
	}
	if !cancelledWorker {
		t.Fatal("ambiguous durable cancellation did not stop the local worker")
	}
}

func TestPersistentServicePreservesCancellationControlWhenDBConfirmsNoCommit(t *testing.T) {
	runID := "66666666-6666-4666-8666-666666666666"
	task := persistentTestTask(runID, 7, TaskRunning)
	repo := newMemoryRunRepository(task)
	repo.appendErr = errors.New("database temporarily unavailable")
	hub := NewTaskHub(time.Hour)
	if err := hub.Register(task, ""); err != nil {
		t.Fatal(err)
	}
	cancelledWorker := false
	if err := hub.SetCancel(runID, func() { cancelledWorker = true }); err != nil {
		t.Fatal(err)
	}
	service := NewPersistentService(nil, nil, nil, repo, t.TempDir(), hub)

	if _, err := service.CancelTask(t.Context(), 7, runID); !errors.Is(err, repo.appendErr) {
		t.Fatalf("cancel error = %v, want append error", err)
	}
	if cancelledWorker {
		t.Fatal("worker was stopped even though the database confirmed cancellation did not commit")
	}
	if _, err := hub.CancelFunc(7, runID); err != nil {
		t.Fatalf("retry lost the local cancellation control: %v", err)
	}
}

func TestPersistentServiceCancellationReleasesGuardWhenWorkerAlreadyStopped(t *testing.T) {
	runID := "88888888-8888-4888-8888-888888888888"
	task := persistentTestTask(runID, 7, TaskRunning)
	repo := newMemoryRunRepository(task)
	hub := NewTaskHub(time.Hour)
	if err := hub.Register(task, ""); err != nil {
		t.Fatal(err)
	}
	if err := hub.SetCancel(runID, func() {}); err != nil {
		t.Fatal(err)
	}
	hub.WorkerDone(runID)
	service := NewPersistentService(nil, nil, nil, repo, t.TempDir(), hub)

	cancelled, err := service.CancelTask(t.Context(), 7, runID)
	if err != nil || cancelled.Status != TaskCancelled {
		t.Fatalf("cancelled task = %#v, err %v", cancelled, err)
	}
	repo.mu.Lock()
	markCalls := repo.markWorkerCalls
	repo.mu.Unlock()
	if markCalls != 1 {
		t.Fatalf("MarkWorkerDone calls = %d, want 1", markCalls)
	}
}

func TestPersistentServiceRetriesTerminalWorkerGuardRelease(t *testing.T) {
	repo := newMemoryRunRepository()
	repo.markWorkerErrors = []error{errors.New("temporary db error"), errors.New("temporary db error"), nil}
	service := NewPersistentService(nil, nil, nil, repo, t.TempDir(), NewTaskHub(time.Hour))
	if err := service.markWorkerDoneWithRetry("99999999-9999-4999-8999-999999999999"); err != nil {
		t.Fatal(err)
	}
	repo.mu.Lock()
	markCalls := repo.markWorkerCalls
	repo.mu.Unlock()
	if markCalls != 3 {
		t.Fatalf("MarkWorkerDone calls = %d, want 3", markCalls)
	}
}

func TestPersistentServiceReconcilesExitedWorkerAfterDatabaseRecovers(t *testing.T) {
	runID := "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
	task := persistentTestTask(runID, 7, TaskRunning)
	repo := newMemoryRunRepository(task)
	repo.appendErrors = []error{errors.New("database unavailable"), errors.New("database unavailable")}
	hub := NewTaskHub(time.Hour)
	if err := hub.Register(task, ""); err != nil {
		t.Fatal(err)
	}
	hub.WorkerDone(runID)
	service := NewPersistentService(nil, nil, nil, repo, t.TempDir(), hub)

	service.reconcileExitedWorker(7, runID)
	reconciled, err := repo.Get(t.Context(), 7, runID)
	if err != nil {
		t.Fatal(err)
	}
	if reconciled.Status != TaskFailed || reconciled.Error == nil || reconciled.Error.Code != "GO_WORKER_EXITED" {
		t.Fatalf("unexpected reconciled task: %#v", reconciled)
	}
	repo.mu.Lock()
	appendCalls := repo.appendCalls
	markCalls := repo.markWorkerCalls
	repo.mu.Unlock()
	if appendCalls != 3 || markCalls != 1 {
		t.Fatalf("append calls = %d, mark calls = %d; want 3 and 1", appendCalls, markCalls)
	}
}

func TestPersistentServiceConfirmedAppendFailurePreservesHotStateWithoutPublishing(t *testing.T) {
	runID := "44444444-4444-4444-8444-444444444444"
	task := persistentTestTask(runID, 7, TaskRunning)
	repo := newMemoryRunRepository(task)
	repo.appendErr = errors.New("database unavailable")
	hub := NewTaskHub(time.Hour)
	if err := hub.Register(task, ""); err != nil {
		t.Fatal(err)
	}
	service := NewPersistentService(nil, nil, nil, repo, t.TempDir(), hub)
	subscription, err := service.SubscribeTask(t.Context(), 7, runID, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer subscription.Close()

	event := persistentTestEvent(task, 1, "event-1", EventTaskStarted, StagePreparing, "started")
	if err := service.publishEvent(t.Context(), event); !errors.Is(err, repo.appendErr) {
		t.Fatalf("publishEvent error = %v, want %v", err, repo.appendErr)
	}
	if repo.appendCalls != 1 {
		t.Fatalf("AppendEvent calls = %d, want 1", repo.appendCalls)
	}
	select {
	case event, open := <-subscription.Events:
		if !open {
			t.Fatal("definite rollback unexpectedly invalidated the retryable hot stream")
		}
		t.Fatalf("subscriber observed uncommitted event: %#v", event)
	case <-time.After(50 * time.Millisecond):
	}
	local, err := hub.Get(7, runID)
	if err != nil {
		t.Fatal(err)
	}
	if local.LastEventSequence != 0 || local.EventID != "" {
		t.Fatalf("hot task advanced after rollback: %#v", local)
	}
	persisted, err := repo.Get(t.Context(), 7, runID)
	if err != nil {
		t.Fatal(err)
	}
	if persisted.LastEventSequence != 0 || persisted.EventID != "" {
		t.Fatalf("repository advanced after persistence failure: %#v", persisted)
	}
}

func TestPersistentServiceUnknownAppendCommitInvalidatesHotStream(t *testing.T) {
	runID := "77777777-7777-4777-8777-777777777777"
	task := persistentTestTask(runID, 7, TaskRunning)
	repo := newMemoryRunRepository(task)
	repo.appendErr = ErrEventCommitUnknown
	hub := NewTaskHub(time.Hour)
	if err := hub.Register(task, ""); err != nil {
		t.Fatal(err)
	}
	cancelled := false
	if err := hub.SetCancel(runID, func() { cancelled = true }); err != nil {
		t.Fatal(err)
	}
	subscription, err := hub.Subscribe(7, runID, 0)
	if err != nil {
		t.Fatal(err)
	}
	service := NewPersistentService(nil, nil, nil, repo, t.TempDir(), hub)

	event := persistentTestEvent(task, 1, "event-1", EventTaskStarted, StagePreparing, "started")
	if err := service.publishEvent(t.Context(), event); !errors.Is(err, ErrEventCommitUnknown) {
		t.Fatalf("publish error = %v, want unknown commit", err)
	}
	if !cancelled {
		t.Fatal("unknown commit outcome did not cancel the divergent worker")
	}
	select {
	case _, open := <-subscription.Events:
		if open {
			t.Fatal("unknown commit outcome left subscriber open")
		}
	case <-time.After(time.Second):
		t.Fatal("unknown commit outcome did not close subscriber")
	}
}

func TestPersistentServiceClassifiesTransientEventStoreFailureAsRetryable(t *testing.T) {
	runtime := &scriptedRuntime{stream: &scriptedStream{events: []AgentEvent{{
		Sequence: 1, Type: EventTaskStarted, Stage: StagePreparing, Message: "started",
	}}}}
	repo := newMemoryRunRepository()
	repo.appendErrors = []error{errors.New("temporary database failure")}
	service := NewPersistentService(
		&stubModelRepo{config: activeModelConfig()},
		&stubAgentRepo{config: activeAgentConfig()},
		runtime,
		repo,
		t.TempDir(),
		NewTaskHub(time.Hour),
	)

	task, err := service.CreateTask(t.Context(), actor.User{UserID: 7}, CreateTaskRequest{
		ThreadID: "event-store-failure-thread", Description: "Build a deck",
	})
	if err != nil {
		t.Fatal(err)
	}
	failed := waitForTask(t, service, 7, task.RunID, TaskFailed)
	if failed.Error == nil || failed.Error.Code != "EVENT_PERSISTENCE_FAILED" || !failed.Error.Retryable {
		t.Fatalf("transient event-store error was misclassified: %#v", failed)
	}
}

func TestPersistentServiceCommitsRuntimeEventsBeforeUpdatingLiveHub(t *testing.T) {
	release := make(chan struct{})
	runtime := &gatedRuntime{release: release, contexts: make(chan context.Context, 1)}
	repo := newMemoryRunRepository()
	hub := NewTaskHub(time.Hour)
	artifactDir := t.TempDir()
	service := NewPersistentService(
		&stubModelRepo{config: activeModelConfig()},
		&stubAgentRepo{config: activeAgentConfig()},
		runtime,
		repo,
		artifactDir,
		hub,
	)

	task, err := service.CreateTask(t.Context(), actor.User{UserID: 7}, CreateTaskRequest{
		ThreadID:        "durable-thread",
		ClientRequestID: "durable-request",
		Description:     "Build a durable architecture deck",
	})
	if err != nil {
		t.Fatal(err)
	}
	<-runtime.contexts
	artifactPath := filepath.Join(artifactDir, task.RunID, "exports", "deck.pptx")
	if err := os.MkdirAll(filepath.Dir(artifactPath), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(artifactPath, []byte("test pptx"), 0o600); err != nil {
		t.Fatal(err)
	}
	subscription, err := service.SubscribeTask(t.Context(), 7, task.RunID, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer subscription.Close()
	close(release)

	completed := waitForTask(t, service, 7, task.RunID, TaskSucceeded)
	if completed.LastEventSequence != 2 || completed.Result == nil {
		t.Fatalf("unexpected durable completion: %#v", completed)
	}
	repo.mu.Lock()
	persistedEvents := append([]AgentEvent(nil), repo.events[task.RunID]...)
	repo.mu.Unlock()
	if len(persistedEvents) != 2 || persistedEvents[0].Type != EventTaskStarted || persistedEvents[1].Type != EventTaskCompleted {
		t.Fatalf("durable events = %#v", persistedEvents)
	}

	for sequence := uint64(1); sequence <= 2; sequence++ {
		select {
		case event := <-subscription.Events:
			if event.Sequence != sequence || persistedEvents[sequence-1].EventID != event.EventID {
				t.Fatalf("live event = %#v, durable event = %#v", event, persistedEvents[sequence-1])
			}
		case <-time.After(time.Second):
			t.Fatalf("timed out waiting for committed live event %d", sequence)
		}
	}
}

func TestPersistentServiceRejectsSucceededSnapshotWithoutDownloadableArtifact(t *testing.T) {
	release := make(chan struct{})
	runtime := &gatedRuntime{release: release, contexts: make(chan context.Context, 1)}
	repo := newMemoryRunRepository()
	service := NewPersistentService(
		&stubModelRepo{config: activeModelConfig()},
		&stubAgentRepo{config: activeAgentConfig()},
		runtime,
		repo,
		t.TempDir(),
		NewTaskHub(time.Hour),
	)

	task, err := service.CreateTask(t.Context(), actor.User{UserID: 7}, CreateTaskRequest{
		ThreadID: "missing-artifact-thread", Description: "Build a deck",
	})
	if err != nil {
		t.Fatal(err)
	}
	<-runtime.contexts
	close(release)
	failed := waitForTask(t, service, 7, task.RunID, TaskFailed)
	if failed.Error == nil || failed.Error.Code != "ARTIFACT_NOT_READY" || failed.Result != nil {
		t.Fatalf("missing artifact produced an invalid durable terminal snapshot: %#v", failed)
	}
}

func TestPersistentServiceRejectsArtifactReadyForMissingFile(t *testing.T) {
	runtime := &scriptedRuntime{stream: &scriptedStream{events: []AgentEvent{
		{Sequence: 1, Type: EventTaskStarted, Stage: StagePreparing, Message: "started"},
		{
			Sequence: 2, Type: EventArtifactReady, Stage: StageRendering, Message: "preview ready",
			Resources: []ResourceRef{{
				ResourceID: "preview-1", Type: ResourcePagePreview, Name: "preview.png", RelativePath: "previews/preview.png",
			}},
		},
	}}}
	repo := newMemoryRunRepository()
	service := NewPersistentService(
		&stubModelRepo{config: activeModelConfig()},
		&stubAgentRepo{config: activeAgentConfig()},
		runtime,
		repo,
		t.TempDir(),
		NewTaskHub(time.Hour),
	)

	task, err := service.CreateTask(t.Context(), actor.User{UserID: 7}, CreateTaskRequest{
		ThreadID: "missing-preview-thread", Description: "Build a deck",
	})
	if err != nil {
		t.Fatal(err)
	}
	failed := waitForTask(t, service, 7, task.RunID, TaskFailed)
	if failed.Error == nil || failed.Error.Code != "ARTIFACT_NOT_READY" || failed.LastEventSequence != 2 {
		t.Fatalf("missing ready artifact produced an invalid durable state: %#v", failed)
	}
}

type persistenceCountingRuntime struct {
	calls chan<- struct{}
}

func (runtime *persistenceCountingRuntime) GeneratePpt(context.Context, RuntimeRequest) (EventStream, error) {
	select {
	case runtime.calls <- struct{}{}:
	default:
	}
	return nil, errors.New("runtime should not have been called")
}

type memoryRunRepository struct {
	mu                  sync.Mutex
	tasks               map[string]*Task
	events              map[string][]AgentEvent
	fingerprints        map[string]string
	forceIdempotentMiss bool
	createResult        *Task
	createCreated       bool
	createCalls         int
	createErr           error
	storeCreateOnError  bool
	createObserved      chan<- Task
	createRelease       <-chan struct{}
	appendErr           error
	appendErrors        []error
	appendCommitErr     error
	appendCalls         int
	markWorkerErrors    []error
	markWorkerCalls     int
}

func newMemoryRunRepository(tasks ...Task) *memoryRunRepository {
	repo := &memoryRunRepository{
		tasks:         make(map[string]*Task),
		events:        make(map[string][]AgentEvent),
		fingerprints:  make(map[string]string),
		createCreated: true,
	}
	for index := range tasks {
		task := tasks[index]
		task.RunID = normalizeTaskID(task.RunID)
		task.TaskID = task.RunID
		repo.tasks[task.RunID] = cloneTask(&task)
	}
	return repo
}

func (repo *memoryRunRepository) FindIdempotent(_ context.Context, ownerID int64, clientRequestID, fingerprint string) (*Task, error) {
	repo.mu.Lock()
	defer repo.mu.Unlock()
	if repo.forceIdempotentMiss || strings.TrimSpace(clientRequestID) == "" {
		return nil, nil
	}
	for _, task := range repo.tasks {
		if task.ownerID != ownerID || task.ClientRequestID != strings.TrimSpace(clientRequestID) {
			continue
		}
		if stored := repo.fingerprints[task.RunID]; stored != "" && stored != strings.TrimSpace(fingerprint) {
			return nil, ErrClientRequestConflict
		}
		return cloneTask(task), nil
	}
	return nil, nil
}

func (repo *memoryRunRepository) CreateOrGet(_ context.Context, task Task, fingerprint string) (*Task, bool, error) {
	repo.mu.Lock()
	defer repo.mu.Unlock()
	repo.createCalls++
	if repo.createObserved != nil {
		repo.createObserved <- task
	}
	if repo.createRelease != nil {
		<-repo.createRelease
	}
	if repo.createErr != nil {
		if repo.storeCreateOnError {
			runID := normalizeTaskID(task.RunID)
			task.RunID = runID
			task.TaskID = runID
			repo.tasks[runID] = cloneTask(&task)
			repo.fingerprints[runID] = strings.TrimSpace(fingerprint)
		}
		return nil, false, repo.createErr
	}
	if repo.createResult != nil {
		return cloneTask(repo.createResult), repo.createCreated, nil
	}
	runID := normalizeTaskID(task.RunID)
	task.RunID = runID
	task.TaskID = runID
	repo.tasks[runID] = cloneTask(&task)
	repo.fingerprints[runID] = strings.TrimSpace(fingerprint)
	return cloneTask(&task), true, nil
}

func (repo *memoryRunRepository) Get(_ context.Context, ownerID int64, runID string) (*Task, error) {
	repo.mu.Lock()
	defer repo.mu.Unlock()
	task := repo.tasks[normalizeTaskID(runID)]
	if task == nil || task.ownerID != ownerID {
		return nil, ErrTaskNotFound
	}
	return cloneTask(task), nil
}

func (repo *memoryRunRepository) List(_ context.Context, ownerID int64, threadID string, limit int) ([]*Task, error) {
	repo.mu.Lock()
	defer repo.mu.Unlock()
	threadID = strings.TrimSpace(threadID)
	if limit <= 0 {
		limit = defaultTaskListLimit
	}
	if limit > maxTaskListLimit {
		limit = maxTaskListLimit
	}
	tasks := make([]*Task, 0, len(repo.tasks))
	for _, task := range repo.tasks {
		if task.ownerID != ownerID || (threadID != "" && task.ThreadID != threadID) {
			continue
		}
		tasks = append(tasks, cloneTask(task))
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

func (repo *memoryRunRepository) AppendEvent(_ context.Context, event AgentEvent, receivedAt time.Time) (*Task, *AgentEvent, error) {
	repo.mu.Lock()
	defer repo.mu.Unlock()
	repo.appendCalls++
	if len(repo.appendErrors) > 0 {
		err := repo.appendErrors[0]
		repo.appendErrors = repo.appendErrors[1:]
		if err != nil {
			return nil, nil, err
		}
	}
	if repo.appendErr != nil {
		return nil, nil, repo.appendErr
	}
	runID := normalizeTaskID(event.RunID)
	if runID == "" {
		runID = normalizeTaskID(event.TaskID)
	}
	task := repo.tasks[runID]
	if task == nil {
		return nil, nil, ErrTaskNotFound
	}
	next, canonical, err := reduceTaskEvent(*task, event, receivedAt)
	if err != nil {
		return nil, nil, err
	}
	repo.tasks[runID] = cloneTask(&next)
	repo.events[runID] = append(repo.events[runID], canonical)
	if repo.appendCommitErr != nil {
		return nil, nil, repo.appendCommitErr
	}
	return cloneTask(&next), &canonical, nil
}

func (repo *memoryRunRepository) ListEventsAfter(_ context.Context, ownerID int64, runID string, after uint64) ([]AgentEvent, error) {
	repo.mu.Lock()
	defer repo.mu.Unlock()
	runID = normalizeTaskID(runID)
	task := repo.tasks[runID]
	if task == nil || task.ownerID != ownerID {
		return nil, ErrTaskNotFound
	}
	events := make([]AgentEvent, 0, len(repo.events[runID]))
	for _, event := range repo.events[runID] {
		if event.Sequence > after {
			events = append(events, event)
		}
	}
	return events, nil
}

func (repo *memoryRunRepository) MarkWorkerDone(context.Context, string, time.Time) error {
	repo.mu.Lock()
	defer repo.mu.Unlock()
	repo.markWorkerCalls++
	if len(repo.markWorkerErrors) == 0 {
		return nil
	}
	err := repo.markWorkerErrors[0]
	repo.markWorkerErrors = repo.markWorkerErrors[1:]
	return err
}

func (repo *memoryRunRepository) TouchHeartbeat(context.Context, string, time.Time) error {
	return nil
}

func (repo *memoryRunRepository) RecoverInterrupted(context.Context, time.Time) (int, error) {
	return 0, nil
}

func persistentTestTask(runID string, ownerID int64, status TaskStatus) Task {
	now := time.Now().UTC().Add(-time.Minute).Truncate(time.Second)
	return Task{
		SchemaVersion: CurrentSchemaVersion,
		ThreadID:      "thread-" + runID,
		RunID:         runID,
		TaskID:        runID,
		Description:   "Persistent deck",
		Status:        status,
		Stage:         StagePreparing,
		Message:       "PPT task created",
		CreatedAt:     now,
		UpdatedAt:     now,
		ownerID:       ownerID,
	}
}

func persistentTestEvent(task Task, sequence uint64, eventID string, eventType EventType, stage Stage, message string) AgentEvent {
	return AgentEvent{
		SchemaVersion: CurrentSchemaVersion,
		EventID:       eventID,
		ThreadID:      task.ThreadID,
		RunID:         task.RunID,
		TaskID:        task.RunID,
		Sequence:      sequence,
		CreatedAt:     task.CreatedAt.Add(time.Duration(sequence) * time.Second),
		Channel:       eventChannelForType(eventType),
		Type:          eventType,
		Stage:         stage,
		Message:       message,
	}
}
