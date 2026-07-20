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

	"agent-task-platform/apps/api/internal/modules/auth"
	"agent-task-platform/apps/api/internal/shared/actor"
	"agent-task-platform/apps/api/internal/transport/http/response"

	"github.com/gin-gonic/gin"
)

func TestCreateTaskBindsMultipartAttachments(t *testing.T) {
	gin.SetMode(gin.TestMode)
	body := &bytes.Buffer{}
	writer := multipart.NewWriter(body)
	_ = writer.WriteField("thread_id", "thread-42")
	_ = writer.WriteField("client_request_id", "request-42")
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
	if service.created.ThreadID != "thread-42" || service.created.ClientRequestID != "request-42" || service.created.Description != "Generate a platform overview" || service.created.AgentConfigID != 42 {
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

func TestListTasksPassesOwnerThreadAndLimit(t *testing.T) {
	gin.SetMode(gin.TestMode)
	service := &fakeTaskService{tasks: []*Task{{
		SchemaVersion: CurrentSchemaVersion,
		ThreadID:      "thread-1",
		RunID:         "run-1",
		TaskID:        "run-1",
		Description:   "Architecture deck",
		AgentConfigID: 42,
	}}}
	recorder := httptest.NewRecorder()
	context, _ := gin.CreateTestContext(recorder)
	context.Request = httptest.NewRequest(http.MethodGet, "/api/v1/ppt/tasks?thread_id=thread-1&limit=7", nil)
	context.Set(auth.ContextKeyClaims, &auth.Claims{UserID: 9})

	NewHandler(service).ListTasks(context)
	if recorder.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", recorder.Code, recorder.Body.String())
	}
	if service.listedOwner != 9 || service.listedThread != "thread-1" || service.listedLimit != 7 {
		t.Fatalf("unexpected list arguments: owner=%d thread=%q limit=%d", service.listedOwner, service.listedThread, service.listedLimit)
	}
	for _, expected := range []string{`"run_id":"run-1"`, `"description":"Architecture deck"`, `"agent_config_id":42`} {
		if !strings.Contains(recorder.Body.String(), expected) {
			t.Fatalf("list response missing %q: %s", expected, recorder.Body.String())
		}
	}
}

func TestListTasksRejectsInvalidLimit(t *testing.T) {
	recorder := httptest.NewRecorder()
	context, _ := gin.CreateTestContext(recorder)
	context.Request = httptest.NewRequest(http.MethodGet, "/api/v1/ppt/tasks?limit=101", nil)
	context.Set(auth.ContextKeyClaims, &auth.Claims{UserID: 9})

	NewHandler(&fakeTaskService{}).ListTasks(context)
	if recorder.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, body = %s", recorder.Code, recorder.Body.String())
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

func TestCreateConflictsExposeStableCodes(t *testing.T) {
	for _, test := range []struct {
		name string
		err  error
		code string
	}{
		{name: "idempotency", err: ErrClientRequestConflict, code: "client_request_conflict"},
		{name: "thread", err: ErrThreadRunConflict, code: "thread_run_conflict"},
		{name: "worker initializing", err: ErrTaskNotReady, code: "run_not_ready"},
	} {
		t.Run(test.name, func(t *testing.T) {
			recorder := httptest.NewRecorder()
			context, _ := gin.CreateTestContext(recorder)
			response.MarkVersioned(context)
			NewHandler(&fakeTaskService{}).respondError(context, test.err)
			if recorder.Code != http.StatusConflict || !strings.Contains(recorder.Body.String(), `"code":"`+test.code+`"`) {
				t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
			}
		})
	}
}

type fakeTaskService struct {
	created      CreateTaskRequest
	task         *Task
	tasks        []*Task
	subscription *Subscription
	err          error
	listedOwner  int64
	listedThread string
	listedLimit  int
}

func (service *fakeTaskService) CreateTask(_ context.Context, _ actor.User, request CreateTaskRequest) (*Task, error) {
	service.created = request
	return service.task, service.err
}
func (service *fakeTaskService) GetTask(context.Context, int64, string) (*Task, error) {
	return service.task, service.err
}
func (service *fakeTaskService) ListTasks(_ context.Context, ownerID int64, threadID string, limit int) ([]*Task, error) {
	service.listedOwner = ownerID
	service.listedThread = threadID
	service.listedLimit = limit
	return service.tasks, service.err
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
