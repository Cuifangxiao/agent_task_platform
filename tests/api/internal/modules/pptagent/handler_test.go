package pptagent

import (
	"bytes"
	"context"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"

	"agent-task-platform/apps/api/internal/modules/auth"
	"agent-task-platform/apps/api/internal/shared/actor"

	"github.com/gin-gonic/gin"
)

func TestCreateTaskBindsMultipartAttachments(t *testing.T) {
	gin.SetMode(gin.TestMode)
	body := &bytes.Buffer{}
	writer := multipart.NewWriter(body)
	_ = writer.WriteField("agent_config_id", "42")
	_ = writer.WriteField("description", "Generate a platform overview")
	_ = writer.WriteField("language", "zh-CN")
	_ = writer.WriteField("expected_slide_count", "8")
	file, _ := writer.CreateFormFile("attachments", "source.md")
	_, _ = file.Write([]byte("# Source"))
	_ = writer.Close()

	service := &fakeTaskService{task: &Task{TaskID: "task-1", Status: TaskRunning}}
	handler := NewHandler(service)
	recorder := httptest.NewRecorder()
	context, _ := gin.CreateTestContext(recorder)
	context.Request = httptest.NewRequest(http.MethodPost, "/api/v1/ppt/tasks", body)
	context.Request.Header.Set("Content-Type", writer.FormDataContentType())
	context.Set(auth.ContextKeyClaims, &auth.Claims{UserID: 7, Username: "tester"})

	handler.CreateTask(context)
	if recorder.Code != http.StatusAccepted {
		t.Fatalf("status = %d, body = %s", recorder.Code, recorder.Body.String())
	}
	if service.created.Description != "Generate a platform overview" || service.created.AgentConfigID != 42 {
		t.Fatalf("unexpected request: %#v", service.created)
	}
	if service.created.Options.ExpectedSlideCount != 8 || len(service.created.Attachments) != 1 {
		t.Fatalf("unexpected options or attachments: %#v", service.created)
	}
	attachment := service.created.Attachments[0]
	if attachment.Name != "source.md" || attachment.SHA256 == "" || string(attachment.Content) != "# Source" {
		t.Fatalf("unexpected attachment: %#v", attachment)
	}
}

func TestSubscribeTaskWritesReplayAsSSEAndStopsAtTerminal(t *testing.T) {
	gin.SetMode(gin.TestMode)
	now := time.Now().UTC()
	service := &fakeTaskService{subscription: &Subscription{
		Replay: []AgentEvent{
			{TaskID: "task-1", Sequence: 1, CreatedAt: now, Type: EventTaskStarted, Stage: StagePreparing, Message: "started"},
			{TaskID: "task-1", Sequence: 2, CreatedAt: now, Type: EventTaskCompleted, Stage: StageFinished, Message: "done"},
		},
		Events: closedEventChannel(),
		Close:  func() {},
	}}
	handler := NewHandler(service)
	recorder := httptest.NewRecorder()
	context, _ := gin.CreateTestContext(recorder)
	context.Params = gin.Params{{Key: "task_id", Value: "task-1"}}
	context.Request = httptest.NewRequest(http.MethodGet, "/api/v1/ppt/tasks/task-1/events", nil)
	context.Set(auth.ContextKeyClaims, &auth.Claims{UserID: 7})

	handler.SubscribeTask(context)
	output := recorder.Body.String()
	for _, expected := range []string{"id: 1", "event: task_started", `"type":"TASK_STARTED"`, "id: 2", "event: task_completed"} {
		if !strings.Contains(output, expected) {
			t.Fatalf("SSE output missing %q: %s", expected, output)
		}
	}
	if strings.Contains(strings.ToLower(output), "version") || strings.Contains(strings.ToLower(output), "review") {
		t.Fatalf("obsolete protocol leaked into SSE: %s", output)
	}
}

func TestCreateTaskRejectsUnsupportedAttachment(t *testing.T) {
	body := &bytes.Buffer{}
	writer := multipart.NewWriter(body)
	_ = writer.WriteField("description", "test")
	file, _ := writer.CreateFormFile("attachments", "malware.exe")
	_, _ = file.Write([]byte("bad"))
	_ = writer.Close()

	recorder := httptest.NewRecorder()
	context, _ := gin.CreateTestContext(recorder)
	context.Request = httptest.NewRequest(http.MethodPost, "/api/v1/ppt/tasks", body)
	context.Request.Header.Set("Content-Type", writer.FormDataContentType())
	context.Set(auth.ContextKeyClaims, &auth.Claims{UserID: 7})
	NewHandler(&fakeTaskService{}).CreateTask(context)
	if recorder.Code != http.StatusUnsupportedMediaType {
		t.Fatalf("status = %d, body = %s", recorder.Code, recorder.Body.String())
	}
}

func TestSubscribeTaskDoesNotSendLiveEventsBehindCursor(t *testing.T) {
	gin.SetMode(gin.TestMode)
	events := make(chan AgentEvent, 1)
	events <- AgentEvent{TaskID: "task-1", Sequence: 3, Type: EventStepProgress, Stage: StagePlanning, Message: "stale"}
	close(events)
	service := &fakeTaskService{subscription: &Subscription{Events: events, Close: func() {}}}
	recorder := httptest.NewRecorder()
	context, _ := gin.CreateTestContext(recorder)
	context.Params = gin.Params{{Key: "task_id", Value: "task-1"}}
	context.Request = httptest.NewRequest(http.MethodGet, "/api/v1/ppt/tasks/task-1/events?after=100", nil)
	context.Set(auth.ContextKeyClaims, &auth.Claims{UserID: 7})

	NewHandler(service).SubscribeTask(context)
	if strings.Contains(recorder.Body.String(), "stale") {
		t.Fatalf("SSE emitted an event behind the requested cursor: %s", recorder.Body.String())
	}
}

func closedEventChannel() <-chan AgentEvent {
	channel := make(chan AgentEvent)
	close(channel)
	return channel
}

type fakeTaskService struct {
	created      CreateTaskRequest
	task         *Task
	subscription *Subscription
	err          error
}

func (service *fakeTaskService) CreateTask(_ context.Context, _ actor.User, request CreateTaskRequest) (*Task, error) {
	service.created = request
	return service.task, service.err
}
func (service *fakeTaskService) GetTask(context.Context, int64, string) (*Task, error) {
	return service.task, service.err
}
func (service *fakeTaskService) SubscribeTask(context.Context, int64, string, uint64) (*Subscription, error) {
	return service.subscription, service.err
}
func (service *fakeTaskService) CancelTask(context.Context, int64, string) (*Task, error) {
	return service.task, service.err
}
func (service *fakeTaskService) OpenArtifact(context.Context, int64, string, string) (*os.File, error) {
	return nil, service.err
}
