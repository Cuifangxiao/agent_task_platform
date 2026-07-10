package pptagent

import (
	"context"
	"errors"
	"io"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"agent-task-platform/internal/agentconfig"
	"agent-task-platform/internal/modelconfig"
	"agent-task-platform/internal/providercredential"
)

const testDeckRequestID = "11111111-1111-4111-8111-111111111111"

func TestDeckJobContinuesAfterSubscriberDisconnectAndReplaysBySequence(t *testing.T) {
	gate := make(chan struct{})
	runtime := &scriptedJobRuntime{generate: &gatedDeckStream{gate: gate, chunks: []*DeckStreamChunk{
		{RequestID: testDeckRequestID, EventType: "stage_started", Stage: "brief", Message: "started"},
		{RequestID: testDeckRequestID, EventType: "artifact", Stage: "export", Message: "ready", Payload: []byte(`{"relative_path":"deck.pptx","warnings":["degraded"]}`)},
		{RequestID: testDeckRequestID, EventType: "stage_progress", Stage: "export", StageStatus: "running", Message: "export metrics recorded"},
		{RequestID: testDeckRequestID, EventType: "done", Stage: "export", StageStatus: "done", Done: true},
	}}}
	service, repo := newJobTestService(t, runtime)
	job, err := service.CreateJob(context.Background(), providercredential.UserContext{UserID: 7, Username: "alice"}, CreateDeckJobRequest{Description: "deck"})
	if err != nil {
		t.Fatal(err)
	}

	subscriberCtx, cancelSubscriber := context.WithCancel(context.Background())
	stream, err := service.SubscribeJob(subscriberCtx, 7, job.ID, 0)
	if err != nil {
		t.Fatal(err)
	}
	first, err := stream.Recv()
	if err != nil || first.EventType != "job_queued" {
		t.Fatalf("first event = %#v, %v", first, err)
	}
	cancelSubscriber()
	close(gate)

	waitForJobStatus(t, repo, job.ID, DeckJobSuccessWithWarnings)
	replay, err := service.SubscribeJob(context.Background(), 7, job.ID, first.Sequence)
	if err != nil {
		t.Fatal(err)
	}
	next, err := replay.Recv()
	if err != nil {
		t.Fatal(err)
	}
	if next.Sequence <= first.Sequence || next.JobID != job.ID {
		t.Fatalf("replayed event does not advance sequence: %#v", next)
	}
	events, err := repo.ListEvents(context.Background(), 7, job.ID, 0, 100)
	if err != nil {
		t.Fatal(err)
	}
	for index, event := range events {
		if event.Sequence != int64(index+1) {
			t.Fatalf("event sequence[%d] = %d", index, event.Sequence)
		}
	}
}

func TestDeckJobWaitingActionIsIdempotentAndCancelPersists(t *testing.T) {
	resumeGate := make(chan struct{})
	runtime := &scriptedJobRuntime{
		generate: &gatedDeckStream{chunks: []*DeckStreamChunk{{
			RequestID: testDeckRequestID, EventType: "interrupt", Stage: "outline_review", StageStatus: "waiting", Done: true,
		}}},
		resume: &gatedDeckStream{gate: resumeGate, chunks: []*DeckStreamChunk{{RequestID: testDeckRequestID, EventType: "done", Stage: "export", Done: true}}},
	}
	service, repo := newJobTestService(t, runtime)
	job, err := service.CreateJob(context.Background(), providercredential.UserContext{UserID: 7, Username: "alice"}, CreateDeckJobRequest{Description: "deck"})
	if err != nil {
		t.Fatal(err)
	}
	waitForJobStatus(t, repo, job.ID, DeckJobWaitingForOutline)
	if err := service.RecoverJobs(context.Background()); err != nil {
		t.Fatal(err)
	}
	waitForJobStatus(t, repo, job.ID, DeckJobWaitingForOutline)

	action := DeckJobActionRequest{Action: "approve_outline", OutlineMarkdown: "# Deck", IdempotencyKey: "approve-v1", Payload: []byte(`{}`)}
	if _, duplicate, err := service.SubmitJobAction(context.Background(), providercredential.UserContext{UserID: 7}, job.ID, action); err != nil || duplicate {
		t.Fatalf("first action duplicate=%v err=%v", duplicate, err)
	}
	if _, duplicate, err := service.SubmitJobAction(context.Background(), providercredential.UserContext{UserID: 7}, job.ID, action); err != nil || !duplicate {
		t.Fatalf("second action duplicate=%v err=%v", duplicate, err)
	}
	cancelled, err := service.CancelJob(context.Background(), 7, job.ID)
	if err != nil {
		t.Fatal(err)
	}
	if cancelled.Status != DeckJobCancelled {
		t.Fatalf("cancel status = %s", cancelled.Status)
	}
	close(resumeGate)
	waitForJobStatus(t, repo, job.ID, DeckJobCancelled)
}

func TestDeckJobTransportFailurePersistsStageAndRetryability(t *testing.T) {
	runtime := &scriptedJobRuntime{generate: &gatedDeckStream{chunks: []*DeckStreamChunk{
		{RequestID: testDeckRequestID, EventType: "stage_started", Stage: "designer"},
	}, finalErr: errors.New("grpc unavailable")}}
	service, repo := newJobTestService(t, runtime)
	job, err := service.CreateJob(context.Background(), providercredential.UserContext{UserID: 7, Username: "alice"}, CreateDeckJobRequest{Description: "deck"})
	if err != nil {
		t.Fatal(err)
	}
	waitForJobStatus(t, repo, job.ID, DeckJobFailed)
	stored, _ := repo.GetJobForWorker(context.Background(), job.ID)
	if stored.Stage != "designer" || stored.ErrorCode != "runtime_transport" || !stored.Retryable || stored.RequestID != testDeckRequestID {
		t.Fatalf("unexpected persisted failure: %#v", stored)
	}
}

func TestArtifactPathRequiresOwningUser(t *testing.T) {
	repo := newMemoryJobRepo()
	dir := t.TempDir()
	requestDir := filepath.Join(dir, testDeckRequestID)
	if err := os.MkdirAll(requestDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(requestDir, "deck.pptx"), []byte("pptx"), 0o600); err != nil {
		t.Fatal(err)
	}
	_, err := repo.CreateJob(context.Background(), DeckJob{
		ID: "job-1", UserID: 7, ConversationID: 30, AgentConfigID: 20, RequestID: testDeckRequestID,
		Status: DeckJobSuccess, Stage: "export", InputJSON: `{}`, QualityJSON: `{}`,
	}, nil)
	if err != nil {
		t.Fatal(err)
	}
	service := NewService(nil, nil, nil, nil, dir, repo)
	if _, err := service.ArtifactAssetPathForUser(context.Background(), 8, testDeckRequestID, ""); !errors.Is(err, ErrArtifactNotFound) {
		t.Fatalf("cross-user artifact error = %v", err)
	}
	path, err := service.ArtifactAssetPathForUser(context.Background(), 7, testDeckRequestID, "")
	if err != nil || filepath.Base(path) != "deck.pptx" {
		t.Fatalf("owner artifact path=%q err=%v", path, err)
	}
	if err := os.WriteFile(filepath.Join(requestDir, "unsafe.html"), []byte(`<script>alert(1)</script>`), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := service.ArtifactAssetPathForUser(context.Background(), 7, testDeckRequestID, "unsafe.html"); !errors.Is(err, ErrArtifactNotFound) {
		t.Fatalf("unsafe HTML error = %v", err)
	}
}

func TestNormalizeDeckStageUsesVersionedProtocolNames(t *testing.T) {
	cases := map[string]string{"slides": "writer", "design_slides": "designer", "review": "review_vlm", "coherence": "coherence", "repair": "repair"}
	for input, expected := range cases {
		if actual := normalizeDeckStage(input); actual != expected {
			t.Fatalf("normalizeDeckStage(%q) = %q, want %q", input, actual, expected)
		}
	}
}

func TestPersistedStageCompletionDoesNotTerminateJobSubscription(t *testing.T) {
	cases := []struct {
		name      string
		event     DeckJobEvent
		wantDone  bool
		wantError string
	}{
		{name: "intermediate done", event: DeckJobEvent{EventType: "stage_progress", StageStatus: "done"}},
		{name: "intermediate error", event: DeckJobEvent{EventType: "stage_progress", StageStatus: "error", Message: "review degraded"}},
		{name: "artifact", event: DeckJobEvent{EventType: "artifact", StageStatus: "done"}},
		{name: "interrupt", event: DeckJobEvent{EventType: "interrupt", StageStatus: "waiting"}, wantDone: true},
		{name: "terminal done", event: DeckJobEvent{EventType: "done", StageStatus: "done"}, wantDone: true},
		{name: "terminal error", event: DeckJobEvent{EventType: "error", StageStatus: "error", Message: "runtime failed"}, wantDone: true, wantError: "runtime failed"},
		{name: "cancelled", event: DeckJobEvent{EventType: "cancelled", StageStatus: "error"}, wantDone: true},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			chunk := eventToChunk(tc.event)
			if chunk.Done != tc.wantDone || chunk.Error != tc.wantError {
				t.Fatalf("chunk Done=%v Error=%q, want Done=%v Error=%q", chunk.Done, chunk.Error, tc.wantDone, tc.wantError)
			}
		})
	}

	if status := jobStatusFromChunk(&DeckStreamChunk{EventType: "stage_progress", Stage: "review_vlm", StageStatus: "error"}); status != DeckJobRunning {
		t.Fatalf("stage-level error status = %s, want %s", status, DeckJobRunning)
	}
}

func TestRegenerateOutlineActionIsAllowedOnlyWhileWaitingForOutline(t *testing.T) {
	if err := validateActionState(DeckJobWaitingForOutline, "regenerate_outline"); err != nil {
		t.Fatal(err)
	}
	if err := validateActionState(DeckJobRunning, "regenerate_outline"); err == nil {
		t.Fatal("expected regenerate_outline to reject a running job")
	}
}

func TestDeckJobPassesControlledSourceReferenceWithoutGRPCBytes(t *testing.T) {
	requests := make(chan RuntimeGenerateDeckRequest, 1)
	runtime := &scriptedJobRuntime{
		generateRequests: requests,
		generate:         &gatedDeckStream{chunks: []*DeckStreamChunk{{RequestID: testDeckRequestID, EventType: "done", Stage: "export", StageStatus: "done", Done: true}}},
	}
	service, repo := newJobTestService(t, runtime)
	content := []byte("source material")
	job, err := service.CreateJob(context.Background(), providercredential.UserContext{UserID: 7, Username: "alice"}, CreateDeckJobRequest{
		Description: "deck", SourceFiles: []SourceFile{{FileName: "report.md", Content: content}},
	})
	if err != nil {
		t.Fatal(err)
	}
	req := <-requests
	if len(req.SourceFiles) != 1 || len(req.SourceFiles[0].Content) != 0 || req.SourceFiles[0].ResourceID == "" || req.SourceFiles[0].LocalPath == "" {
		t.Fatalf("runtime source was not externalized: %#v", req.SourceFiles)
	}
	stored, err := os.ReadFile(req.SourceFiles[0].LocalPath)
	if err != nil || string(stored) != string(content) {
		t.Fatalf("controlled source content=%q err=%v", stored, err)
	}
	sources, err := repo.ListSources(context.Background(), job.ID)
	if err != nil || len(sources) != 1 || len(sources[0].Content) != 0 || sources[0].SHA256 == "" {
		t.Fatalf("persisted source metadata=%#v err=%v", sources, err)
	}
}

func TestSubscriptionDisconnectAndResumeArePersistedAsProductSignals(t *testing.T) {
	gate := make(chan struct{})
	service, repo := newJobTestService(t, &scriptedJobRuntime{generate: &gatedDeckStream{gate: gate, chunks: []*DeckStreamChunk{{RequestID: testDeckRequestID, EventType: "done", Stage: "export", Done: true}}}})
	job, err := service.CreateJob(context.Background(), providercredential.UserContext{UserID: 7, Username: "alice"}, CreateDeckJobRequest{Description: "deck"})
	if err != nil {
		t.Fatal(err)
	}
	service.RecordJobSubscriptionDisconnect(context.Background(), 7, job.ID)
	service.RecordJobSubscriptionResume(context.Background(), 7, job.ID, 1)
	events, err := repo.ListEvents(context.Background(), 7, job.ID, 0, 100)
	if err != nil {
		t.Fatal(err)
	}
	seen := map[string]bool{}
	for _, event := range events {
		seen[event.EventType] = true
	}
	if !seen["subscription_disconnected"] || !seen["subscription_resumed"] {
		t.Fatalf("subscription metric events=%v", seen)
	}
	close(gate)
}

func newJobTestService(t *testing.T, runtime RuntimeClient) (*Service, *memoryJobRepo) {
	t.Helper()
	agentRepo := &fakeAgentConfigRepo{
		agent:  &agentconfig.AgentDefinition{ID: 10, AgentKey: agentconfig.AgentKeyPPT, Status: agentconfig.StatusActive, Nodes: []agentconfig.NodeDefinition{{Key: "writer", RequiresModel: true}}},
		config: &agentconfig.AgentConfig{ID: 20, UserID: 7, AgentID: 10, AgentKey: agentconfig.AgentKeyPPT, Name: "PPT", Status: agentconfig.StatusActive, NodeBindings: []agentconfig.NodeBinding{{NodeKey: "writer", ModelConfigID: 100}}},
	}
	modelRepo := &fakeModelConfigRepo{config: &modelconfig.ModelConfig{ID: 100, UserID: 7, Provider: "openai", ModelName: "gpt-4o", APIKey: "test", ProviderOptions: `{}`, MaxTokens: 4096, Status: modelconfig.StatusActive}}
	repo := newMemoryJobRepo()
	history := &fakeHistoryRepo{outlineMessage: &ConversationMessage{ID: 90, ConversationID: 30, Kind: "outline", RequestID: testDeckRequestID, ContentJSON: `{"awaitingConfirm":true}`}}
	return NewService(modelRepo, agentRepo, history, runtime, t.TempDir(), repo), repo
}

func waitForJobStatus(t *testing.T, repo *memoryJobRepo, jobID string, expected DeckJobStatus) {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		job, err := repo.GetJobForWorker(context.Background(), jobID)
		if err == nil && job.Status == expected {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	job, _ := repo.GetJobForWorker(context.Background(), jobID)
	t.Fatalf("job status = %s, want %s", job.Status, expected)
}

type scriptedJobRuntime struct {
	generate         DeckStream
	resume           DeckStream
	generateRequests chan<- RuntimeGenerateDeckRequest
}

func (r *scriptedJobRuntime) GenerateDeck(_ context.Context, _ providercredential.UserContext, req RuntimeGenerateDeckRequest) (DeckStream, error) {
	if r.generateRequests != nil {
		r.generateRequests <- req
	}
	return r.generate, nil
}

func (r *scriptedJobRuntime) ResumeDeck(context.Context, providercredential.UserContext, RuntimeResumeDeckRequest) (DeckStream, error) {
	return r.resume, nil
}

type gatedDeckStream struct {
	gate     <-chan struct{}
	chunks   []*DeckStreamChunk
	index    int
	finalErr error
}

func (s *gatedDeckStream) Recv() (*DeckStreamChunk, error) {
	if s.gate != nil {
		<-s.gate
		s.gate = nil
	}
	if s.index < len(s.chunks) {
		chunk := s.chunks[s.index]
		s.index++
		return chunk, nil
	}
	if s.finalErr != nil {
		err := s.finalErr
		s.finalErr = nil
		return nil, err
	}
	return nil, io.EOF
}

func (s *gatedDeckStream) Close() error { return nil }

type memoryJobRepo struct {
	mu      sync.Mutex
	jobs    map[string]*DeckJob
	sources map[string][]DeckJobSource
	events  map[string][]DeckJobEvent
	actions map[string]bool
}

func newMemoryJobRepo() *memoryJobRepo {
	return &memoryJobRepo{jobs: map[string]*DeckJob{}, sources: map[string][]DeckJobSource{}, events: map[string][]DeckJobEvent{}, actions: map[string]bool{}}
}

func (r *memoryJobRepo) CreateJob(_ context.Context, job DeckJob, sources []DeckJobSource) (*DeckJob, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	now := time.Now()
	job.CreatedAt, job.UpdatedAt = now, now
	r.jobs[job.ID] = cloneDeckJob(&job)
	r.sources[job.ID] = append([]DeckJobSource(nil), sources...)
	return cloneDeckJob(&job), nil
}

func (r *memoryJobRepo) GetJobByID(_ context.Context, userID int64, jobID string) (*DeckJob, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	job := r.jobs[jobID]
	if job == nil || job.UserID != userID {
		return nil, ErrDeckJobNotFound
	}
	return cloneDeckJob(job), nil
}

func (r *memoryJobRepo) GetJobByRequestID(_ context.Context, userID int64, requestID string) (*DeckJob, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	for _, job := range r.jobs {
		if job.UserID == userID && job.RequestID == requestID {
			return cloneDeckJob(job), nil
		}
	}
	return nil, ErrDeckJobNotFound
}

func (r *memoryJobRepo) GetJobForWorker(_ context.Context, jobID string) (*DeckJob, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.jobs[jobID] == nil {
		return nil, ErrDeckJobNotFound
	}
	return cloneDeckJob(r.jobs[jobID]), nil
}

func (r *memoryJobRepo) GetLatestJobByConversation(_ context.Context, userID, conversationID int64) (*DeckJob, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	for _, job := range r.jobs {
		if job.UserID == userID && job.ConversationID == conversationID {
			return cloneDeckJob(job), nil
		}
	}
	return nil, ErrDeckJobNotFound
}

func (r *memoryJobRepo) ListRecoverableJobs(context.Context) ([]*DeckJob, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	var result []*DeckJob
	for _, job := range r.jobs {
		if job.Status == DeckJobQueued || job.Status == DeckJobRunning || job.Status == DeckJobExporting {
			result = append(result, cloneDeckJob(job))
		}
	}
	return result, nil
}

func (r *memoryJobRepo) ListSources(_ context.Context, jobID string) ([]DeckJobSource, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]DeckJobSource(nil), r.sources[jobID]...), nil
}

func (r *memoryJobRepo) UpdateJob(_ context.Context, jobID string, status DeckJobStatus, stage, requestID, errorCode, errorSummary string, retryable bool) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	job := r.jobs[jobID]
	if job == nil {
		return ErrDeckJobNotFound
	}
	job.Status, job.Stage, job.ErrorCode, job.ErrorSummary, job.Retryable = status, stage, errorCode, errorSummary, retryable
	if requestID != "" {
		job.RequestID = requestID
	}
	job.UpdatedAt = time.Now()
	return nil
}

func (r *memoryJobRepo) UpdateJobQuality(_ context.Context, jobID string, qualityJSON string) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	job := r.jobs[jobID]
	if job == nil {
		return ErrDeckJobNotFound
	}
	job.QualityJSON = qualityJSON
	return nil
}

func (r *memoryJobRepo) RequestCancel(_ context.Context, userID int64, jobID string) (*DeckJob, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	job := r.jobs[jobID]
	if job == nil || job.UserID != userID {
		return nil, ErrDeckJobNotFound
	}
	job.CancelRequested, job.Status = true, DeckJobCancelled
	return cloneDeckJob(job), nil
}

func (r *memoryJobRepo) AppendEvent(_ context.Context, event DeckJobEvent) (*DeckJobEvent, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	job := r.jobs[event.JobID]
	if job == nil {
		return nil, ErrDeckJobNotFound
	}
	event.Sequence = int64(len(r.events[event.JobID]) + 1)
	event.EventVersion = DeckEventVersion
	event.CreatedAt = time.Now()
	event.PayloadValue = decodeJSONValue(event.Payload)
	r.events[event.JobID] = append(r.events[event.JobID], event)
	job.LastEventSequence = event.Sequence
	copy := event
	return &copy, nil
}

func (r *memoryJobRepo) ListEvents(_ context.Context, userID int64, jobID string, after int64, limit int) ([]DeckJobEvent, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	job := r.jobs[jobID]
	if job == nil || job.UserID != userID {
		return nil, ErrDeckJobNotFound
	}
	var result []DeckJobEvent
	for _, event := range r.events[jobID] {
		if event.Sequence > after {
			result = append(result, event)
			if limit > 0 && len(result) >= limit {
				break
			}
		}
	}
	return result, nil
}

func (r *memoryJobRepo) CreateAction(_ context.Context, userID int64, jobID, key, action, payload string) (bool, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	job := r.jobs[jobID]
	if job == nil || job.UserID != userID {
		return false, ErrDeckJobNotFound
	}
	compound := jobID + ":" + key
	if r.actions[compound] {
		return false, nil
	}
	r.actions[compound] = true
	return true, nil
}

func (r *memoryJobRepo) UpdateActionStatus(context.Context, string, string, string) error { return nil }
func (r *memoryJobRepo) UpsertAsset(context.Context, DeckJobAsset) error                  { return nil }

func cloneDeckJob(job *DeckJob) *DeckJob { copy := *job; return &copy }
