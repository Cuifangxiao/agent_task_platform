package pptagent

import (
	"context"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"agent-task-platform/apps/api/internal/modules/agentconfig"
	"agent-task-platform/apps/api/internal/modules/modelconfig"
	"agent-task-platform/apps/api/internal/shared/actor"
)

func TestPPTModelNodesMatchThreeStageRuntimeContract(t *testing.T) {
	if got, want := strings.Join(pptModelNodes[:], ","), "outline,style,designer"; got != want {
		t.Fatalf("ppt model nodes = %q, want %q", got, want)
	}
}

func TestCreateTaskUsesIndependentContextAndCompletes(t *testing.T) {
	release := make(chan struct{})
	runtime := &gatedRuntime{release: release, contexts: make(chan context.Context, 1)}
	service := NewService(
		&stubModelRepo{config: activeModelConfig()},
		&stubAgentRepo{config: activeAgentConfig()},
		runtime,
		t.TempDir(),
		NewTaskHub(time.Hour),
	)
	requestContext := context.WithValue(context.Background(), requestContextKey("trace_id"), "trace-123")
	requestContext, cancelRequest := context.WithCancel(requestContext)
	task, err := service.CreateTask(requestContext, actor.User{UserID: 7}, CreateTaskRequest{Description: "Build an architecture deck"})
	if err != nil {
		t.Fatalf("CreateTask returned error: %v", err)
	}
	cancelRequest()

	runtimeContext := <-runtime.contexts
	if value := runtimeContext.Value(requestContextKey("trace_id")); value != "trace-123" {
		t.Fatalf("task context lost request value: %#v", value)
	}
	select {
	case <-runtimeContext.Done():
		t.Fatal("task context was cancelled with the POST request")
	case <-time.After(20 * time.Millisecond):
	}
	if task.SchemaVersion != CurrentSchemaVersion || task.EventID != "" || task.ThreadID == "" || task.RunID == "" || task.TaskID != task.RunID {
		t.Fatalf("unexpected task envelope: %#v", task)
	}
	if task.Name != "Build an architecture deck" || task.Description != "Build an architecture deck" || task.AgentConfigID != 3 {
		t.Fatalf("request metadata was not retained: %#v", task)
	}
	if runtime.request.ThreadID != task.ThreadID || runtime.request.RunID != task.RunID || runtime.request.TaskID != task.RunID {
		t.Fatalf("runtime request did not receive thread/run aliases: %#v", runtime.request)
	}
	close(release)

	completed := waitForTask(t, service, 7, task.TaskID, TaskSucceeded)
	if completed.Result == nil || completed.Result.PPTFile.URL != "/api/v1/ppt/tasks/"+task.TaskID+"/file" {
		t.Fatalf("unexpected completed result: %#v", completed.Result)
	}
	if len(runtime.request.Models) != len(pptModelNodes) {
		t.Fatalf("runtime received %d models, want %d", len(runtime.request.Models), len(pptModelNodes))
	}
	modelNodes := make([]string, 0, len(runtime.request.Models))
	for _, model := range runtime.request.Models {
		modelNodes = append(modelNodes, model.NodeKey)
	}
	if got, want := strings.Join(modelNodes, ","), "outline,style,designer"; got != want {
		t.Fatalf("runtime model nodes = %q, want %q", got, want)
	}
}

func TestCreateTaskIsIdempotentAndRejectsConflictingReuse(t *testing.T) {
	release := make(chan struct{})
	runtime := &gatedRuntime{release: release, contexts: make(chan context.Context, 2)}
	service := NewService(
		&stubModelRepo{config: activeModelConfig()},
		&stubAgentRepo{config: activeAgentConfig()},
		runtime,
		t.TempDir(),
		NewTaskHub(time.Hour),
	)
	request := CreateTaskRequest{
		ThreadID:        "thread-1",
		ClientRequestID: "request-1",
		Description:     "Build an architecture deck",
	}
	first, err := service.CreateTask(context.Background(), actor.User{UserID: 7}, request)
	if err != nil {
		t.Fatal(err)
	}
	<-runtime.contexts
	second, err := service.CreateTask(context.Background(), actor.User{UserID: 7}, request)
	if err != nil {
		t.Fatal(err)
	}
	if second.RunID != first.RunID || second.ThreadID != first.ThreadID {
		t.Fatalf("idempotent retry created a different task: first=%#v second=%#v", first, second)
	}
	select {
	case <-runtime.contexts:
		t.Fatal("idempotent retry started a second runtime worker")
	case <-time.After(20 * time.Millisecond):
	}

	conflictingRequest := request
	conflictingRequest.Description = "Build a different deck"
	if _, err := service.CreateTask(context.Background(), actor.User{UserID: 7}, conflictingRequest); !errors.Is(err, ErrClientRequestConflict) {
		t.Fatalf("conflicting idempotency reuse error = %v", err)
	}
	sameThreadRequest := request
	sameThreadRequest.ClientRequestID = "request-2"
	if _, err := service.CreateTask(context.Background(), actor.User{UserID: 7}, sameThreadRequest); !errors.Is(err, ErrThreadRunConflict) {
		t.Fatalf("same-thread active run error = %v", err)
	}

	close(release)
	waitForTask(t, service, 7, first.RunID, TaskSucceeded)
}

func TestRunTaskTurnsUnexpectedEOFIntoTerminalFailure(t *testing.T) {
	hub := NewTaskHub(time.Hour)
	task, err := hub.Create("11111111-1111-4111-8111-111111111111", 9, time.Now().UTC())
	if err != nil {
		t.Fatal(err)
	}
	service := &Service{
		runtimeClient: &scriptedRuntime{stream: &scriptedStream{events: []AgentEvent{{
			TaskID: task.TaskID, Sequence: 1, Type: EventTaskStarted, Stage: StagePreparing, Message: "started",
		}}}},
		hub: hub,
	}
	service.runTask(context.Background(), 9, RuntimeRequest{TaskID: task.TaskID})

	failed, err := hub.Get(9, task.TaskID)
	if err != nil {
		t.Fatal(err)
	}
	if failed.Status != TaskFailed || failed.Error == nil || failed.Error.Code != "PYTHON_STREAM_LOST" {
		t.Fatalf("unexpected failed task: %#v", failed)
	}
	if failed.LastEventSequence != 2 {
		t.Fatalf("last sequence = %d, want 2", failed.LastEventSequence)
	}
}

func TestRunTaskRejectsOutOfOrderRuntimeEvents(t *testing.T) {
	hub := NewTaskHub(time.Hour)
	task, _ := hub.Create("22222222-2222-4222-8222-222222222222", 5, time.Now().UTC())
	service := &Service{
		runtimeClient: &scriptedRuntime{stream: &scriptedStream{events: []AgentEvent{{
			TaskID: task.TaskID, Sequence: 2, Type: EventStepProgress, Stage: StagePlanning, Message: "bad sequence",
		}}}},
		hub: hub,
	}
	service.runTask(context.Background(), 5, RuntimeRequest{TaskID: task.TaskID})

	failed, _ := hub.Get(5, task.TaskID)
	if failed.Status != TaskFailed || failed.Error == nil || failed.Error.Code != "INVALID_RUNTIME_EVENT" {
		t.Fatalf("unexpected protocol failure: %#v", failed)
	}
}

func TestRunTaskRejectsCompletedEventWithoutPPTResult(t *testing.T) {
	hub := NewTaskHub(time.Hour)
	task, _ := hub.Create("77777777-7777-4777-8777-777777777777", 5, time.Now().UTC())
	service := &Service{
		runtimeClient: &scriptedRuntime{stream: &scriptedStream{events: []AgentEvent{
			{TaskID: task.TaskID, Sequence: 1, Type: EventTaskStarted, Stage: StagePreparing, Message: "started"},
			{TaskID: task.TaskID, Sequence: 2, Type: EventTaskCompleted, Stage: StageFinished, Message: "done"},
		}}},
		hub: hub,
	}
	service.runTask(context.Background(), 5, RuntimeRequest{TaskID: task.TaskID})

	failed, _ := hub.Get(5, task.TaskID)
	if failed.Status != TaskFailed || failed.Error == nil || failed.Error.Code != "INVALID_RUNTIME_EVENT" {
		t.Fatalf("invalid completion was accepted: %#v", failed)
	}
}

func TestRunTaskRejectsArtifactPathTraversal(t *testing.T) {
	hub := NewTaskHub(time.Hour)
	task, _ := hub.Create("88888888-8888-4888-8888-888888888888", 5, time.Now().UTC())
	service := &Service{
		runtimeClient: &scriptedRuntime{stream: &scriptedStream{events: []AgentEvent{{
			TaskID: task.TaskID, Sequence: 1, Type: EventArtifactReady, Stage: StageRendering, Message: "preview",
			Resources: []ResourceRef{{ResourceID: "preview", Type: ResourcePagePreview, Name: "escape.png", RelativePath: "../escape.png"}},
		}}}},
		hub: hub,
	}
	service.runTask(context.Background(), 5, RuntimeRequest{TaskID: task.TaskID})

	failed, _ := hub.Get(5, task.TaskID)
	if failed.Status != TaskFailed || failed.Error == nil || failed.Error.Code != "INVALID_RUNTIME_EVENT" {
		t.Fatalf("path traversal event was accepted: %#v", failed)
	}
}

func TestRunTaskRedactsRuntimeDebugDetails(t *testing.T) {
	hub := NewTaskHub(time.Hour)
	task, _ := hub.Create("99999999-9999-4999-8999-999999999999", 5, time.Now().UTC())
	service := &Service{
		runtimeClient: &scriptedRuntime{stream: &scriptedStream{events: []AgentEvent{{
			TaskID: task.TaskID, Sequence: 1, Type: EventTaskFailed, Stage: StagePreparing,
			Message: "api_key=supersecret", Error: &ErrorDetail{
				Code: "MODEL_ERROR", Message: "api_key=supersecret", DebugMessage: "Bearer secret-token",
			},
		}}}},
		hub: hub,
	}
	service.runTask(context.Background(), 5, RuntimeRequest{TaskID: task.TaskID})

	failed, _ := hub.Get(5, task.TaskID)
	joined := failed.Message + " " + failed.Error.Message + " " + failed.Error.DebugMessage
	if strings.Contains(joined, "supersecret") || strings.Contains(joined, "secret-token") {
		t.Fatalf("runtime secret leaked through task state: %q", joined)
	}
	if failed.Error.DebugMessage != "" {
		t.Fatalf("debug detail was exposed to task clients: %q", failed.Error.DebugMessage)
	}
}

func TestArtifactPathRejectsSymlinkOutsideTaskDirectory(t *testing.T) {
	artifactDir := t.TempDir()
	taskID := "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
	taskDir := filepath.Join(artifactDir, taskID)
	if err := os.MkdirAll(taskDir, 0o755); err != nil {
		t.Fatal(err)
	}
	outsideDir := t.TempDir()
	outsideFile := filepath.Join(outsideDir, "outside.pptx")
	if err := os.WriteFile(outsideFile, []byte("not a real deck"), 0o600); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(taskDir, "deck.pptx")
	if err := os.Symlink(outsideFile, link); err != nil {
		t.Skipf("symlink creation is unavailable: %v", err)
	}

	hub := NewTaskHub(time.Hour)
	_, _ = hub.Create(taskID, 5, time.Now().UTC())
	if err := hub.Publish(taskID, AgentEvent{
		TaskID: taskID, Sequence: 1, Type: EventTaskStarted, Stage: StagePreparing, Message: "started",
	}); err != nil {
		t.Fatal(err)
	}
	if err := hub.Publish(taskID, AgentEvent{
		TaskID: taskID, Sequence: 2, Type: EventTaskCompleted, Stage: StageFinished, Message: "done",
		FinalResult: &FinalResult{PPTFile: ResourceRef{
			ResourceID: "pptx", Type: ResourcePPTX, Name: "deck.pptx", RelativePath: "deck.pptx",
		}},
	}); err != nil {
		t.Fatal(err)
	}
	service := &Service{hub: hub, artifactDir: artifactDir}
	if _, err := service.OpenArtifact(context.Background(), 5, taskID, ""); !errors.Is(err, ErrArtifactNotFound) {
		t.Fatalf("symlink escape error = %v", err)
	}
}

func TestCreateTaskRejectsNegativeAgentConfigID(t *testing.T) {
	service := &Service{}
	_, err := service.CreateTask(context.Background(), actor.User{UserID: 5}, CreateTaskRequest{
		AgentConfigID: -1,
		Description:   "deck",
	})
	if !errors.Is(err, ErrInvalidRequest) {
		t.Fatalf("negative agent_config_id error = %v", err)
	}
}

func TestCancelTaskNormalizesTaskIDBeforeReturningSnapshot(t *testing.T) {
	hub := NewTaskHub(time.Hour)
	taskID := "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
	_, _ = hub.Create(taskID, 5, time.Now().UTC())
	service := &Service{hub: hub}

	task, err := service.CancelTask(context.Background(), 5, "  BBBBBBBB-BBBB-4BBB-8BBB-BBBBBBBBBBBB  ")
	if err != nil {
		t.Fatal(err)
	}
	if task.Status != TaskCancelled || task.TaskID != taskID {
		t.Fatalf("unexpected cancelled snapshot: %#v", task)
	}
}

func waitForTask(t *testing.T, service *Service, ownerID int64, taskID string, expected TaskStatus) *Task {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		task, err := service.GetTask(context.Background(), ownerID, taskID)
		if err == nil && task.Status == expected {
			return task
		}
		time.Sleep(10 * time.Millisecond)
	}
	task, _ := service.GetTask(context.Background(), ownerID, taskID)
	t.Fatalf("task did not reach %s: %#v", expected, task)
	return nil
}

type gatedRuntime struct {
	contexts chan context.Context
	release  <-chan struct{}
	request  RuntimeRequest
}

type requestContextKey string

func (runtime *gatedRuntime) GeneratePpt(ctx context.Context, request RuntimeRequest) (EventStream, error) {
	if runtime.contexts == nil {
		runtime.contexts = make(chan context.Context, 1)
	}
	runtime.request = request
	runtime.contexts <- ctx
	return &gatedStream{ctx: ctx, release: runtime.release, taskID: request.TaskID}, nil
}

type gatedStream struct {
	ctx     context.Context
	release <-chan struct{}
	taskID  string
	index   int
}

func (stream *gatedStream) Recv() (*AgentEvent, error) {
	if stream.index == 0 {
		select {
		case <-stream.ctx.Done():
			return nil, stream.ctx.Err()
		case <-stream.release:
		}
	}
	events := []AgentEvent{
		{TaskID: stream.taskID, Sequence: 1, Type: EventTaskStarted, Stage: StagePreparing, Message: "started"},
		{TaskID: stream.taskID, Sequence: 2, Type: EventTaskCompleted, Stage: StageFinished, Message: "done", FinalResult: &FinalResult{
			PPTFile: ResourceRef{ResourceID: "ppt", Type: ResourcePPTX, Name: "deck.pptx", RelativePath: "exports/deck.pptx"}, SlideCount: 3,
		}},
	}
	if stream.index >= len(events) {
		return nil, io.EOF
	}
	event := events[stream.index]
	stream.index++
	return &event, nil
}

func (*gatedStream) Close() error { return nil }

type scriptedRuntime struct {
	stream EventStream
	err    error
}

func (runtime *scriptedRuntime) GeneratePpt(context.Context, RuntimeRequest) (EventStream, error) {
	return runtime.stream, runtime.err
}

type scriptedStream struct {
	mu     sync.Mutex
	events []AgentEvent
	index  int
	err    error
}

func (stream *scriptedStream) Recv() (*AgentEvent, error) {
	stream.mu.Lock()
	defer stream.mu.Unlock()
	if stream.index >= len(stream.events) {
		if stream.err != nil {
			err := stream.err
			stream.err = nil
			return nil, err
		}
		return nil, io.EOF
	}
	event := stream.events[stream.index]
	stream.index++
	return &event, nil
}

func (*scriptedStream) Close() error { return nil }

type stubModelRepo struct{ config *modelconfig.ModelConfig }

func (*stubModelRepo) Create(context.Context, modelconfig.ModelConfig) (*modelconfig.ModelConfig, error) {
	return nil, errors.New("not implemented")
}
func (*stubModelRepo) ListByUserID(context.Context, int64) ([]*modelconfig.ModelConfig, error) {
	return nil, errors.New("not implemented")
}
func (repo *stubModelRepo) GetByID(context.Context, int64, int64) (*modelconfig.ModelConfig, error) {
	copy := *repo.config
	return &copy, nil
}
func (*stubModelRepo) Update(context.Context, modelconfig.ModelConfig) (*modelconfig.ModelConfig, error) {
	return nil, errors.New("not implemented")
}
func (*stubModelRepo) Delete(context.Context, int64, int64) error {
	return errors.New("not implemented")
}

type stubAgentRepo struct{ config *agentconfig.AgentConfig }

func (*stubAgentRepo) ListAgents(context.Context) ([]*agentconfig.AgentDefinition, error) {
	return nil, errors.New("not implemented")
}
func (*stubAgentRepo) GetAgentByID(context.Context, int64) (*agentconfig.AgentDefinition, error) {
	return nil, errors.New("not implemented")
}
func (*stubAgentRepo) CreateConfig(context.Context, agentconfig.AgentConfig) (*agentconfig.AgentConfig, error) {
	return nil, errors.New("not implemented")
}
func (repo *stubAgentRepo) ListConfigsByUserID(context.Context, int64, int64) ([]*agentconfig.AgentConfig, error) {
	return []*agentconfig.AgentConfig{repo.config}, nil
}
func (repo *stubAgentRepo) GetConfigByID(context.Context, int64, int64) (*agentconfig.AgentConfig, error) {
	return repo.config, nil
}
func (*stubAgentRepo) UpdateConfig(context.Context, agentconfig.AgentConfig) (*agentconfig.AgentConfig, error) {
	return nil, errors.New("not implemented")
}
func (*stubAgentRepo) DeleteConfig(context.Context, int64, int64) error {
	return errors.New("not implemented")
}

func activeAgentConfig() *agentconfig.AgentConfig {
	bindings := make([]agentconfig.NodeBinding, 0, len(pptModelNodes))
	for _, nodeKey := range pptModelNodes {
		bindings = append(bindings, agentconfig.NodeBinding{NodeKey: nodeKey, ModelConfigID: 12})
	}
	return &agentconfig.AgentConfig{
		ID: 3, UserID: 7, AgentID: 1, AgentKey: agentconfig.AgentKeyPPT, Status: agentconfig.StatusActive,
		IsDefault: true, NodeBindings: bindings,
	}
}

func activeModelConfig() *modelconfig.ModelConfig {
	return &modelconfig.ModelConfig{
		ID: 12, UserID: 7, Provider: "openai", ModelName: "gpt-4o", ModelType: modelconfig.ModelTypeMultimodal,
		APIKey: "secret", ProviderOptions: `{"reasoning_effort":"low"}`, Temperature: 0.3, MaxTokens: 4096, Status: modelconfig.StatusActive,
	}
}
