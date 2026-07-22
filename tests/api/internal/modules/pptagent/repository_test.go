package pptagent

import (
	"database/sql/driver"
	"encoding/json"
	"errors"
	"regexp"
	"testing"
	"time"

	"github.com/DATA-DOG/go-sqlmock"
	mysqlDriver "github.com/go-sql-driver/mysql"
)

const (
	testRunID       = "11111111-1111-4111-8111-111111111111"
	testOtherRunID  = "22222222-2222-4222-8222-222222222222"
	testEventID     = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
	testFingerprint = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
)

var persistedRunColumns = []string{
	"run_id", "owner_id", "thread_id", "client_request_id", "request_fingerprint",
	"agent_config_id", "description", "schema_version", "event_id", "status", "stage",
	"message", "last_event_sequence", "result_json", "error_json", "file_url",
	"worker_active", "heartbeat_at", "created_at", "updated_at", "finished_at",
	"worker_finished_at",
}

func TestRunRepositoryCreateStoresNullableRequestFields(t *testing.T) {
	db, mock, err := sqlmock.New()
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	repo := NewRunRepository(db)
	now := time.Now().UTC().Truncate(time.Microsecond)
	task := repositoryTask(testRunID, now)
	task.ClientRequestID = ""
	task.AgentConfigID = 0
	optionsJSON := []byte("{}")

	mock.ExpectExec(regexp.QuoteMeta(insertRunQuery)).
		WithArgs(
			task.RunID, task.ownerID, task.ThreadID, nil, nil, nil, task.Description,
			optionsJSON, task.SchemaVersion, nil, task.Status, task.Stage, task.Message,
			task.LastEventSequence, nil, nil, task.FileURL, task.UpdatedAt,
			task.CreatedAt, task.UpdatedAt, nil,
		).
		WillReturnResult(sqlmock.NewResult(0, 1))

	created, inserted, err := repo.CreateOrGet(t.Context(), task, "")
	if err != nil {
		t.Fatalf("CreateOrGet returned error: %v", err)
	}
	if !inserted || created.RunID != task.RunID || created.ownerID != task.ownerID {
		t.Fatalf("unexpected create result: task=%#v inserted=%v", created, inserted)
	}
	if err := mock.ExpectationsWereMet(); err != nil {
		t.Fatal(err)
	}
}

func TestRunRepositoryDuplicateClientRequestReturnsExistingOrConflict(t *testing.T) {
	for _, test := range []struct {
		name              string
		storedFingerprint string
		wantConflict      bool
	}{
		{name: "same fingerprint", storedFingerprint: testFingerprint},
		{name: "different fingerprint", storedFingerprint: "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff", wantConflict: true},
	} {
		t.Run(test.name, func(t *testing.T) {
			db, mock, err := sqlmock.New()
			if err != nil {
				t.Fatal(err)
			}
			defer db.Close()
			repo := NewRunRepository(db)
			now := time.Now().UTC().Truncate(time.Microsecond)
			task := repositoryTask(testRunID, now)
			task.ClientRequestID = "request-1"

			mock.ExpectExec(regexp.QuoteMeta(insertRunQuery)).
				WillReturnError(&mysqlDriver.MySQLError{Number: 1062, Message: "Duplicate entry for uk_ppt_runs_owner_client_request"})
			stored := repositoryTask(testOtherRunID, now.Add(-time.Minute))
			stored.ClientRequestID = task.ClientRequestID
			mock.ExpectQuery(regexp.QuoteMeta(selectRunColumns+` WHERE owner_id = ? AND client_request_id = ? LIMIT 1`)).
				WithArgs(task.ownerID, task.ClientRequestID).
				WillReturnRows(runRows(stored, test.storedFingerprint, false))

			got, inserted, err := repo.CreateOrGet(t.Context(), task, testFingerprint)
			if test.wantConflict {
				if !errors.Is(err, ErrClientRequestConflict) || got != nil || inserted {
					t.Fatalf("conflict result: task=%#v inserted=%v err=%v", got, inserted, err)
				}
			} else if err != nil || inserted || got == nil || got.RunID != stored.RunID {
				t.Fatalf("idempotent result: task=%#v inserted=%v err=%v", got, inserted, err)
			}
			if err := mock.ExpectationsWereMet(); err != nil {
				t.Fatal(err)
			}
		})
	}
}

func TestRunRepositoryDuplicateActiveThreadReturnsConflict(t *testing.T) {
	db, mock, err := sqlmock.New()
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	repo := NewRunRepository(db)
	now := time.Now().UTC().Truncate(time.Microsecond)
	task := repositoryTask(testRunID, now)

	mock.ExpectExec(regexp.QuoteMeta(insertRunQuery)).
		WillReturnError(&mysqlDriver.MySQLError{Number: 1062, Message: "Duplicate entry for uk_ppt_runs_owner_active_thread"})
	active := repositoryTask(testOtherRunID, now.Add(-time.Minute))
	mock.ExpectQuery(regexp.QuoteMeta(selectRunColumns+` WHERE owner_id = ? AND thread_id = ? AND worker_active = 1 LIMIT 1`)).
		WithArgs(task.ownerID, task.ThreadID).
		WillReturnRows(runRows(active, "", true))

	got, inserted, err := repo.CreateOrGet(t.Context(), task, "")
	if !errors.Is(err, ErrThreadRunConflict) || got != nil || inserted {
		t.Fatalf("active-thread result: task=%#v inserted=%v err=%v", got, inserted, err)
	}
	if err := mock.ExpectationsWereMet(); err != nil {
		t.Fatal(err)
	}
}

func TestRunRepositoryAppendEventCommitsEventAndTerminalSnapshotTogether(t *testing.T) {
	db, mock, err := sqlmock.New()
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	repo := NewRunRepository(db)
	now := time.Now().UTC().Truncate(time.Microsecond)
	previous := repositoryTask(testRunID, now.Add(-time.Minute))
	event := AgentEvent{
		EventID:  testEventID,
		RunID:    previous.RunID,
		TaskID:   previous.RunID,
		Sequence: 1,
		Type:     EventTaskFailed,
		Stage:    StagePreparing,
		Message:  "runtime unavailable",
		Error: &ErrorDetail{
			Code: "RUNTIME_UNAVAILABLE", Message: "runtime unavailable", Retryable: true,
		},
	}

	mock.ExpectBegin()
	mock.ExpectQuery(regexp.QuoteMeta(selectRunColumns + ` WHERE run_id = ? FOR UPDATE`)).
		WithArgs(previous.RunID).
		WillReturnRows(runRows(previous, "", true))
	mock.ExpectExec(regexp.QuoteMeta(insertRunEventQuery)).
		WithArgs(
			event.RunID, uint64(1), event.EventID, CurrentSchemaVersion,
			event.Type, EventChannelLifecycle, event.Stage, sqlmock.AnyArg(), now,
		).
		WillReturnResult(sqlmock.NewResult(0, 1))
	mock.ExpectExec(regexp.QuoteMeta(updateRunSnapshotQuery)).
		WithArgs(
			CurrentSchemaVersion, event.EventID, TaskFailed, event.Stage, event.Message,
			uint64(1), nil, sqlmock.AnyArg(), "", now, now, sqlmock.AnyArg(),
			previous.RunID, uint64(0), TaskRunning,
		).
		WillReturnResult(sqlmock.NewResult(0, 1))
	mock.ExpectCommit()

	task, canonical, err := repo.AppendEvent(t.Context(), event, now)
	if err != nil {
		t.Fatalf("AppendEvent returned error: %v", err)
	}
	if task.Status != TaskFailed || task.LastEventSequence != 1 || task.FinishedAt == nil {
		t.Fatalf("unexpected terminal snapshot: %#v", task)
	}
	if canonical.EventID != event.EventID || canonical.Channel != EventChannelLifecycle || canonical.CreatedAt != now {
		t.Fatalf("unexpected canonical event: %#v", canonical)
	}
	if err := mock.ExpectationsWereMet(); err != nil {
		t.Fatal(err)
	}
}

func TestAgentEventPersistenceJSONIncludesArtifactRevision(t *testing.T) {
	want := "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
	payload, err := json.Marshal(AgentEvent{Revision: want})
	if err != nil {
		t.Fatal(err)
	}
	var persisted map[string]any
	if err := json.Unmarshal(payload, &persisted); err != nil {
		t.Fatal(err)
	}
	if got := persisted["revision"]; got != want {
		t.Fatalf("persisted revision = %#v, want %q", got, want)
	}

	var restored AgentEvent
	if err := json.Unmarshal(payload, &restored); err != nil {
		t.Fatal(err)
	}
	if restored.Revision != want {
		t.Fatalf("restored revision = %q, want %q", restored.Revision, want)
	}
}

func TestRunRepositoryAppendEventRecoversAmbiguousCommit(t *testing.T) {
	db, mock, err := sqlmock.New()
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	repo := NewRunRepository(db)
	now := time.Now().UTC().Truncate(time.Microsecond)
	previous := repositoryTask(testRunID, now.Add(-time.Minute))
	event := AgentEvent{
		EventID: testEventID, RunID: previous.RunID, TaskID: previous.RunID, Sequence: 1,
		Type: EventTaskStarted, Stage: StagePreparing, Message: "started", CreatedAt: now,
	}
	next, canonical, err := reduceTaskEvent(previous, event, now)
	if err != nil {
		t.Fatal(err)
	}
	payload, err := json.Marshal(canonical)
	if err != nil {
		t.Fatal(err)
	}

	mock.ExpectBegin()
	mock.ExpectQuery(regexp.QuoteMeta(selectRunColumns + ` WHERE run_id = ? FOR UPDATE`)).
		WithArgs(previous.RunID).
		WillReturnRows(runRows(previous, "", true))
	mock.ExpectExec(regexp.QuoteMeta(insertRunEventQuery)).
		WillReturnResult(sqlmock.NewResult(0, 1))
	mock.ExpectExec(regexp.QuoteMeta(updateRunSnapshotQuery)).
		WillReturnResult(sqlmock.NewResult(0, 1))
	mock.ExpectCommit().WillReturnError(errors.New("connection lost after commit"))
	mock.ExpectQuery(regexp.QuoteMeta(`SELECT payload_json FROM ppt_run_events WHERE run_id = ? AND sequence = ? AND event_id = ? LIMIT 1`)).
		WithArgs(canonical.RunID, canonical.Sequence, canonical.EventID).
		WillReturnRows(sqlmock.NewRows([]string{"payload_json"}).AddRow(payload))
	mock.ExpectQuery(regexp.QuoteMeta(selectRunColumns+` WHERE owner_id = ? AND run_id = ? LIMIT 1`)).
		WithArgs(previous.ownerID, previous.RunID).
		WillReturnRows(runRows(next, "", true))

	task, committed, err := repo.AppendEvent(t.Context(), event, now)
	if err != nil {
		t.Fatalf("ambiguous committed event was not recovered: %v", err)
	}
	if task.LastEventSequence != 1 || committed.EventID != canonical.EventID {
		t.Fatalf("unexpected recovered commit: task=%#v event=%#v", task, committed)
	}
	if err := mock.ExpectationsWereMet(); err != nil {
		t.Fatal(err)
	}
}

func TestRunRepositoryAppendEventRollsBackWhenSnapshotCASLoses(t *testing.T) {
	db, mock, err := sqlmock.New()
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	repo := NewRunRepository(db)
	now := time.Now().UTC().Truncate(time.Microsecond)
	previous := repositoryTask(testRunID, now.Add(-time.Minute))
	event := AgentEvent{
		EventID: testEventID, RunID: previous.RunID, Sequence: 1,
		Type: EventTaskStarted, Stage: StagePreparing, Message: "started",
	}

	mock.ExpectBegin()
	mock.ExpectQuery(regexp.QuoteMeta(selectRunColumns + ` WHERE run_id = ? FOR UPDATE`)).
		WithArgs(previous.RunID).
		WillReturnRows(runRows(previous, "", true))
	mock.ExpectExec(regexp.QuoteMeta(insertRunEventQuery)).
		WillReturnResult(sqlmock.NewResult(0, 1))
	mock.ExpectExec(regexp.QuoteMeta(updateRunSnapshotQuery)).
		WillReturnResult(sqlmock.NewResult(0, 0))
	mock.ExpectRollback()

	if _, _, err := repo.AppendEvent(t.Context(), event, now); !errors.Is(err, ErrInvalidRequest) {
		t.Fatalf("CAS error = %v", err)
	}
	if err := mock.ExpectationsWereMet(); err != nil {
		t.Fatal(err)
	}
}

func TestRunRepositoryListEventsAfterChecksOwnerAndReturnsCanonicalPayload(t *testing.T) {
	db, mock, err := sqlmock.New()
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	repo := NewRunRepository(db)
	now := time.Now().UTC().Truncate(time.Microsecond)
	task := repositoryTask(testRunID, now)
	task.LastEventSequence = 2
	task.EventID = testEventID
	mock.ExpectQuery(regexp.QuoteMeta(selectRunColumns+` WHERE owner_id = ? AND run_id = ? LIMIT 1`)).
		WithArgs(task.ownerID, task.RunID).
		WillReturnRows(runRows(task, "", true))

	event := AgentEvent{
		SchemaVersion: CurrentSchemaVersion, EventID: testEventID, ThreadID: task.ThreadID,
		RunID: task.RunID, TaskID: task.RunID, Sequence: 2, CreatedAt: now,
		Channel: EventChannelUpdates, Type: EventStepProgress, Stage: StagePlanning,
		Message: "planning",
	}
	payload, _ := json.Marshal(event)
	mock.ExpectQuery(`(?s)SELECT e\.payload_json.*WHERE r\.owner_id = \? AND e\.run_id = \? AND e\.sequence > \?.*ORDER BY e\.sequence ASC`).
		WithArgs(task.ownerID, task.RunID, uint64(1)).
		WillReturnRows(sqlmock.NewRows([]string{"payload_json"}).AddRow(payload))

	events, err := repo.ListEventsAfter(t.Context(), task.ownerID, task.RunID, 1)
	if err != nil || len(events) != 1 || events[0].EventID != event.EventID || events[0].Sequence != 2 {
		t.Fatalf("events=%#v err=%v", events, err)
	}
	if err := mock.ExpectationsWereMet(); err != nil {
		t.Fatal(err)
	}
}

func TestRunRepositoryWorkerLifecycleUpdatesAreIdempotentAndOwnerIndependent(t *testing.T) {
	db, mock, err := sqlmock.New()
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	repo := NewRunRepository(db)
	now := time.Now().UTC().Truncate(time.Microsecond)

	mock.ExpectExec(regexp.QuoteMeta(`UPDATE ppt_runs SET heartbeat_at = ? WHERE run_id = ? AND worker_active = 1`)).
		WithArgs(now, testRunID).
		WillReturnResult(sqlmock.NewResult(0, 1))
	if err := repo.TouchHeartbeat(t.Context(), testRunID, now); err != nil {
		t.Fatalf("TouchHeartbeat returned error: %v", err)
	}

	mock.ExpectExec(regexp.QuoteMeta(`UPDATE ppt_runs SET worker_active = 0, worker_finished_at = ? WHERE run_id = ? AND worker_active = 1 AND status IN ('succeeded', 'failed', 'cancelled')`)).
		WithArgs(now, testRunID).
		WillReturnResult(sqlmock.NewResult(0, 1))
	if err := repo.MarkWorkerDone(t.Context(), testRunID, now); err != nil {
		t.Fatalf("MarkWorkerDone returned error: %v", err)
	}

	// A deferred cleanup can safely run twice. Zero affected rows are accepted
	// only after proving that the run still exists.
	mock.ExpectExec(regexp.QuoteMeta(`UPDATE ppt_runs SET worker_active = 0, worker_finished_at = ? WHERE run_id = ? AND worker_active = 1 AND status IN ('succeeded', 'failed', 'cancelled')`)).
		WithArgs(now, testRunID).
		WillReturnResult(sqlmock.NewResult(0, 0))
	mock.ExpectQuery(regexp.QuoteMeta(`SELECT status FROM ppt_runs WHERE run_id = ? LIMIT 1`)).
		WithArgs(testRunID).
		WillReturnRows(sqlmock.NewRows([]string{"status"}).AddRow(TaskSucceeded))
	if err := repo.MarkWorkerDone(t.Context(), testRunID, now); err != nil {
		t.Fatalf("idempotent MarkWorkerDone returned error: %v", err)
	}

	mock.ExpectExec(regexp.QuoteMeta(`UPDATE ppt_runs SET worker_active = 0, worker_finished_at = ? WHERE run_id = ? AND worker_active = 1 AND status IN ('succeeded', 'failed', 'cancelled')`)).
		WithArgs(now, testOtherRunID).
		WillReturnResult(sqlmock.NewResult(0, 0))
	mock.ExpectQuery(regexp.QuoteMeta(`SELECT status FROM ppt_runs WHERE run_id = ? LIMIT 1`)).
		WithArgs(testOtherRunID).
		WillReturnRows(sqlmock.NewRows([]string{"status"}).AddRow(TaskRunning))
	if err := repo.MarkWorkerDone(t.Context(), testOtherRunID, now); !errors.Is(err, ErrInvalidRequest) {
		t.Fatalf("running MarkWorkerDone error = %v, want ErrInvalidRequest", err)
	}

	if err := mock.ExpectationsWereMet(); err != nil {
		t.Fatal(err)
	}
}

func TestRunRepositoryRecoverInterruptedFailsRunningAndReleasesTerminalWorkers(t *testing.T) {
	db, mock, err := sqlmock.New()
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	repo := NewRunRepository(db)
	now := time.Now().UTC().Truncate(time.Microsecond)
	running := repositoryTask(testRunID, now.Add(-time.Hour))
	terminal := repositoryTask(testOtherRunID, now.Add(-2*time.Hour))
	terminal.Status = TaskSucceeded
	terminal.Stage = StageFinished
	terminal.Message = "done"
	terminal.LastEventSequence = 2
	terminal.EventID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
	terminal.FinishedAt = timePointer(now.Add(-time.Minute))
	terminal.Result = &FinalResult{PPTFile: ResourceRef{
		ResourceID: "ppt", Type: ResourcePPTX, Name: "deck.pptx", RelativePath: "exports/deck.pptx",
	}}

	mock.ExpectBegin()
	mock.ExpectQuery(regexp.QuoteMeta(selectRunColumns + ` WHERE worker_active = 1 FOR UPDATE`)).
		WillReturnRows(runRows(running, "", true).AddRow(runRowValues(terminal, "", true)...))
	mock.ExpectExec(regexp.QuoteMeta(insertRunEventQuery)).
		WithArgs(
			running.RunID, uint64(1), sqlmock.AnyArg(), CurrentSchemaVersion,
			EventTaskFailed, EventChannelLifecycle, running.Stage, sqlmock.AnyArg(), now,
		).
		WillReturnResult(sqlmock.NewResult(0, 1))
	mock.ExpectExec(regexp.QuoteMeta(updateRecoveredRunSnapshotQuery)).
		WithArgs(
			CurrentSchemaVersion, sqlmock.AnyArg(), TaskFailed, running.Stage,
			"PPT task was interrupted by an API restart", uint64(1), nil,
			sqlmock.AnyArg(), "", now, now, sqlmock.AnyArg(), now,
			running.RunID, uint64(0), TaskRunning,
		).
		WillReturnResult(sqlmock.NewResult(0, 1))
	mock.ExpectExec(regexp.QuoteMeta(`UPDATE ppt_runs SET worker_active = 0, worker_finished_at = ? WHERE run_id = ? AND worker_active = 1`)).
		WithArgs(now, terminal.RunID).
		WillReturnResult(sqlmock.NewResult(0, 1))
	mock.ExpectCommit()

	count, err := repo.RecoverInterrupted(t.Context(), now)
	if err != nil || count != 2 {
		t.Fatalf("RecoverInterrupted count=%d err=%v", count, err)
	}
	if err := mock.ExpectationsWereMet(); err != nil {
		t.Fatal(err)
	}
}

func repositoryTask(runID string, now time.Time) Task {
	return Task{
		SchemaVersion: CurrentSchemaVersion,
		ThreadID:      "thread-1",
		RunID:         runID,
		TaskID:        runID,
		Description:   "Build a durable deck",
		Status:        TaskRunning,
		Stage:         StagePreparing,
		Message:       "PPT task created",
		CreatedAt:     now,
		UpdatedAt:     now,
		ownerID:       7,
	}
}

func runRows(task Task, fingerprint string, workerActive bool) *sqlmock.Rows {
	return sqlmock.NewRows(persistedRunColumns).AddRow(runRowValues(task, fingerprint, workerActive)...)
}

func runRowValues(task Task, fingerprint string, workerActive bool) []driver.Value {
	var resultJSON driver.Value
	if task.Result != nil {
		resultJSON, _ = json.Marshal(task.Result)
	}
	var errorJSON driver.Value
	if task.Error != nil {
		errorJSON, _ = json.Marshal(task.Error)
	}
	var clientRequestID driver.Value
	if task.ClientRequestID != "" {
		clientRequestID = task.ClientRequestID
	}
	var storedFingerprint driver.Value
	if fingerprint != "" {
		storedFingerprint = fingerprint
	}
	var agentConfigID driver.Value
	if task.AgentConfigID > 0 {
		agentConfigID = task.AgentConfigID
	}
	var eventID driver.Value
	if task.EventID != "" {
		eventID = task.EventID
	}
	var finishedAt driver.Value
	if task.FinishedAt != nil {
		finishedAt = *task.FinishedAt
	}
	return []driver.Value{
		task.RunID, task.ownerID, task.ThreadID, clientRequestID, storedFingerprint,
		agentConfigID, task.Description, task.SchemaVersion, eventID, string(task.Status),
		string(task.Stage), task.Message, task.LastEventSequence, resultJSON, errorJSON,
		task.FileURL, workerActive, task.UpdatedAt, task.CreatedAt, task.UpdatedAt,
		finishedAt, nil,
	}
}

func timePointer(value time.Time) *time.Time {
	return &value
}
