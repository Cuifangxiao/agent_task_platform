package pptagent

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"reflect"
	"strings"
	"time"

	mysqlDriver "github.com/go-sql-driver/mysql"
)

const selectRunColumns = `
	SELECT
		run_id, owner_id, thread_id, client_request_id, request_fingerprint,
		agent_config_id, presentation_name, description, schema_version, event_id, status, stage,
		message, last_event_sequence, result_json, error_json, file_url,
		worker_active, heartbeat_at, created_at, updated_at, finished_at,
		worker_finished_at
	FROM ppt_runs
`

const insertRunQuery = `
	INSERT INTO ppt_runs (
		run_id, owner_id, thread_id, client_request_id, request_fingerprint,
		agent_config_id, presentation_name, description, options_json, schema_version, event_id,
		status, stage, message, last_event_sequence, result_json, error_json,
		file_url, worker_active, heartbeat_at, created_at, updated_at, finished_at
	)
	VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
`

const insertRunEventQuery = `
	INSERT INTO ppt_run_events (
		run_id, sequence, event_id, schema_version, event_type, channel, stage,
		payload_json, created_at
	)
	VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
`

const updateRunSnapshotQuery = `
	UPDATE ppt_runs
	SET
		schema_version = ?, event_id = ?, status = ?, stage = ?, message = ?,
		last_event_sequence = ?, result_json = ?, error_json = ?, file_url = ?,
		heartbeat_at = ?, updated_at = ?, finished_at = ?
	WHERE run_id = ? AND last_event_sequence = ? AND status = ?
`

const updateRecoveredRunSnapshotQuery = `
	UPDATE ppt_runs
	SET
		schema_version = ?, event_id = ?, status = ?, stage = ?, message = ?,
		last_event_sequence = ?, result_json = ?, error_json = ?, file_url = ?,
		heartbeat_at = ?, updated_at = ?, finished_at = ?, worker_active = 0,
		worker_finished_at = ?
	WHERE run_id = ? AND last_event_sequence = ? AND status = ? AND worker_active = 1
`

// RunRepository stores the durable run snapshot and its complete canonical
// event log. Live WebSocket fan-out remains the responsibility of TaskHub.
type RunRepository interface {
	FindIdempotent(context.Context, int64, string, string) (*Task, error)
	CreateOrGet(context.Context, Task, string) (*Task, bool, error)
	Get(context.Context, int64, string) (*Task, error)
	List(context.Context, int64, string, int) ([]*Task, error)
	Delete(context.Context, int64, string) error
	AppendEvent(context.Context, AgentEvent, time.Time) (*Task, *AgentEvent, error)
	ListEventsAfter(context.Context, int64, string, uint64) ([]AgentEvent, error)
	MarkWorkerDone(context.Context, string, time.Time) error
	TouchHeartbeat(context.Context, string, time.Time) error
	RecoverInterrupted(context.Context, time.Time) (int, error)
}

type mysqlRunRepository struct {
	db *sql.DB
}

type persistedRun struct {
	task               Task
	requestFingerprint string
	workerActive       bool
}

type runScanner interface {
	Scan(...any) error
}

func NewRunRepository(db *sql.DB) RunRepository {
	return &mysqlRunRepository{db: db}
}

func (r *mysqlRunRepository) FindIdempotent(ctx context.Context, ownerID int64, clientRequestID, fingerprint string) (*Task, error) {
	clientRequestID = strings.TrimSpace(clientRequestID)
	fingerprint = strings.TrimSpace(fingerprint)
	if clientRequestID == "" {
		return nil, nil
	}
	if r == nil || r.db == nil || ownerID <= 0 || fingerprint == "" {
		return nil, fmt.Errorf("%w: owner, client_request_id, and request fingerprint are required", ErrInvalidRequest)
	}

	stored, err := scanPersistedRun(r.db.QueryRowContext(
		ctx,
		selectRunColumns+` WHERE owner_id = ? AND client_request_id = ? LIMIT 1`,
		ownerID,
		clientRequestID,
	))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("find idempotent ppt run: %w", err)
	}
	if stored.requestFingerprint != fingerprint {
		return nil, fmt.Errorf("%w: client_request_id=%s", ErrClientRequestConflict, clientRequestID)
	}
	return cloneTask(&stored.task), nil
}

// CreateOrGet atomically relies on MySQL uniqueness for durable idempotency
// and the one-worker-per-owner/thread invariant. The bool is true only when a
// new row was inserted.
func (r *mysqlRunRepository) CreateOrGet(ctx context.Context, task Task, fingerprint string) (*Task, bool, error) {
	if r == nil || r.db == nil {
		return nil, false, fmt.Errorf("%w: run repository is unavailable", ErrInvalidRequest)
	}
	prepared, err := prepareRunForCreate(task, fingerprint)
	if err != nil {
		return nil, false, err
	}
	task = prepared.task
	fingerprint = prepared.requestFingerprint

	// Keep the legacy non-null database column stable while the public and
	// runtime request contracts no longer accept presentation options.
	optionsJSON := []byte("{}")
	resultJSON, err := marshalOptionalJSON(task.Result)
	if err != nil {
		return nil, false, fmt.Errorf("marshal ppt run result: %w", err)
	}
	errorJSON, err := marshalOptionalJSON(task.Error)
	if err != nil {
		return nil, false, fmt.Errorf("marshal ppt run error: %w", err)
	}

	_, err = r.db.ExecContext(
		ctx,
		insertRunQuery,
		task.RunID,
		task.ownerID,
		task.ThreadID,
		nullableString(task.ClientRequestID),
		nullableString(fingerprint),
		nullablePositiveInt64(task.AgentConfigID),
		task.Name,
		task.Description,
		optionsJSON,
		task.SchemaVersion,
		nullableString(task.EventID),
		task.Status,
		task.Stage,
		task.Message,
		task.LastEventSequence,
		resultJSON,
		errorJSON,
		task.FileURL,
		task.UpdatedAt,
		task.CreatedAt,
		task.UpdatedAt,
		task.FinishedAt,
	)
	if err == nil {
		return cloneTask(&task), true, nil
	}
	if !isMySQLDuplicate(err) {
		return nil, false, fmt.Errorf("create ppt run: %w", err)
	}

	if task.ClientRequestID != "" {
		existing, lookupErr := r.FindIdempotent(ctx, task.ownerID, task.ClientRequestID, fingerprint)
		if lookupErr != nil || existing != nil {
			return existing, false, lookupErr
		}
	}
	active, lookupErr := r.findActiveThread(ctx, task.ownerID, task.ThreadID)
	if lookupErr != nil {
		return nil, false, lookupErr
	}
	if active != nil {
		return nil, false, fmt.Errorf("%w: thread_id=%s", ErrThreadRunConflict, task.ThreadID)
	}
	return nil, false, fmt.Errorf("%w: duplicate run_id=%s", ErrInvalidRequest, task.RunID)
}

func (r *mysqlRunRepository) Get(ctx context.Context, ownerID int64, runID string) (*Task, error) {
	if r == nil || r.db == nil || ownerID <= 0 {
		return nil, ErrTaskNotFound
	}
	runID = normalizeTaskID(runID)
	if runID == "" {
		return nil, ErrTaskNotFound
	}
	stored, err := scanPersistedRun(r.db.QueryRowContext(
		ctx,
		selectRunColumns+` WHERE owner_id = ? AND run_id = ? LIMIT 1`,
		ownerID,
		runID,
	))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrTaskNotFound
	}
	if err != nil {
		return nil, fmt.Errorf("get ppt run: %w", err)
	}
	return cloneTask(&stored.task), nil
}

func (r *mysqlRunRepository) List(ctx context.Context, ownerID int64, threadID string, limit int) ([]*Task, error) {
	if r == nil || r.db == nil || ownerID <= 0 {
		return nil, fmt.Errorf("%w: owner is required", ErrInvalidRequest)
	}
	if limit <= 0 {
		limit = defaultTaskListLimit
	}
	if limit > maxTaskListLimit {
		limit = maxTaskListLimit
	}
	threadID = strings.TrimSpace(threadID)
	query := selectRunColumns + ` WHERE owner_id = ?`
	args := []any{ownerID}
	if threadID != "" {
		query += ` AND thread_id = ?`
		args = append(args, threadID)
	}
	query += ` ORDER BY created_at DESC, run_id DESC LIMIT ?`
	args = append(args, limit)

	rows, err := r.db.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, fmt.Errorf("list ppt runs: %w", err)
	}
	defer rows.Close()

	tasks := make([]*Task, 0)
	for rows.Next() {
		stored, scanErr := scanPersistedRun(rows)
		if scanErr != nil {
			return nil, fmt.Errorf("scan listed ppt run: %w", scanErr)
		}
		tasks = append(tasks, cloneTask(&stored.task))
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate ppt runs: %w", err)
	}
	return tasks, nil
}

func (r *mysqlRunRepository) Delete(ctx context.Context, ownerID int64, runID string) error {
	if r == nil || r.db == nil || ownerID <= 0 {
		return ErrTaskNotFound
	}
	runID = normalizeTaskID(runID)
	if runID == "" {
		return ErrTaskNotFound
	}
	result, err := r.db.ExecContext(
		ctx,
		`DELETE FROM ppt_runs WHERE owner_id = ? AND run_id = ? AND status IN ('succeeded', 'failed', 'cancelled') AND worker_active = 0`,
		ownerID,
		runID,
	)
	if err != nil {
		return fmt.Errorf("delete ppt run: %w", err)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return fmt.Errorf("read deleted ppt run rows: %w", err)
	}
	if affected > 0 {
		return nil
	}

	var found int
	if err := r.db.QueryRowContext(
		ctx,
		`SELECT 1 FROM ppt_runs WHERE owner_id = ? AND run_id = ? LIMIT 1`,
		ownerID,
		runID,
	).Scan(&found); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return ErrTaskNotFound
		}
		return fmt.Errorf("check deletable ppt run: %w", err)
	}
	return ErrTaskStillRunning
}

// AppendEvent locks the run, reduces one canonical state transition, and
// commits the event plus updated snapshot in the same transaction. Callers
// may publish the returned event only after this method succeeds.
func (r *mysqlRunRepository) AppendEvent(ctx context.Context, event AgentEvent, receivedAt time.Time) (*Task, *AgentEvent, error) {
	if r == nil || r.db == nil {
		return nil, nil, fmt.Errorf("%w: run repository is unavailable", ErrInvalidRequest)
	}
	runID := normalizeTaskID(event.RunID)
	if runID == "" {
		runID = normalizeTaskID(event.TaskID)
	}
	if runID == "" {
		return nil, nil, fmt.Errorf("%w: event run id is required", ErrInvalidRequest)
	}
	receivedAt = normalizedTime(receivedAt)

	tx, err := r.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, nil, fmt.Errorf("begin append ppt event: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	stored, err := scanPersistedRun(tx.QueryRowContext(
		ctx,
		selectRunColumns+` WHERE run_id = ? FOR UPDATE`,
		runID,
	))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil, ErrTaskNotFound
	}
	if err != nil {
		return nil, nil, fmt.Errorf("lock ppt run: %w", err)
	}
	previous := stored.task
	next, canonical, err := reduceTaskEvent(previous, event, receivedAt)
	if err != nil {
		return nil, nil, err
	}
	if err := insertCanonicalEvent(ctx, tx, canonical); err != nil {
		return nil, nil, err
	}
	if err := updateRunSnapshot(ctx, tx, previous, next, false, time.Time{}); err != nil {
		return nil, nil, err
	}
	if err := tx.Commit(); err != nil {
		if committedTask, committedEvent, ok := r.findCommittedEventAfterCommitError(ctx, previous.ownerID, canonical); ok {
			return committedTask, committedEvent, nil
		}
		return nil, nil, fmt.Errorf("%w: %v", ErrEventCommitUnknown, err)
	}
	next.cancel = nil
	return &next, &canonical, nil
}

func (r *mysqlRunRepository) findCommittedEventAfterCommitError(parent context.Context, ownerID int64, event AgentEvent) (*Task, *AgentEvent, bool) {
	lookupCtx, cancel := context.WithTimeout(context.WithoutCancel(parent), persistenceTimeout)
	defer cancel()
	delay := 25 * time.Millisecond
	for {
		var payload []byte
		err := r.db.QueryRowContext(
			lookupCtx,
			`SELECT payload_json FROM ppt_run_events WHERE run_id = ? AND sequence = ? AND event_id = ? LIMIT 1`,
			event.RunID,
			event.Sequence,
			event.EventID,
		).Scan(&payload)
		if err == nil {
			var committed AgentEvent
			if json.Unmarshal(payload, &committed) == nil && committed.EventID == event.EventID && committed.Sequence == event.Sequence {
				task, taskErr := r.Get(lookupCtx, ownerID, event.RunID)
				if taskErr == nil && task.LastEventSequence >= event.Sequence {
					return task, &committed, true
				}
			}
		}
		var ready bool
		delay, ready = waitForPersistenceRetry(lookupCtx, delay)
		if !ready {
			return nil, nil, false
		}
	}
}

func (r *mysqlRunRepository) ListEventsAfter(ctx context.Context, ownerID int64, runID string, after uint64) ([]AgentEvent, error) {
	task, err := r.Get(ctx, ownerID, runID)
	if err != nil {
		return nil, err
	}
	if after > task.LastEventSequence {
		return nil, fmt.Errorf("%w: after=%d last_event_sequence=%d", ErrEventCursorAhead, after, task.LastEventSequence)
	}

	rows, err := r.db.QueryContext(
		ctx,
		`
			SELECT e.payload_json
			FROM ppt_run_events AS e
			INNER JOIN ppt_runs AS r ON r.run_id = e.run_id
			WHERE r.owner_id = ? AND e.run_id = ? AND e.sequence > ?
			ORDER BY e.sequence ASC
		`,
		ownerID,
		normalizeTaskID(runID),
		after,
	)
	if err != nil {
		return nil, fmt.Errorf("list ppt run events: %w", err)
	}
	defer rows.Close()

	events := make([]AgentEvent, 0)
	for rows.Next() {
		var payload []byte
		if err := rows.Scan(&payload); err != nil {
			return nil, fmt.Errorf("scan ppt run event: %w", err)
		}
		var event AgentEvent
		if err := json.Unmarshal(payload, &event); err != nil {
			return nil, fmt.Errorf("decode ppt run event: %w", err)
		}
		events = append(events, event)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate ppt run events: %w", err)
	}
	return events, nil
}

func (r *mysqlRunRepository) MarkWorkerDone(ctx context.Context, runID string, finishedAt time.Time) error {
	if r == nil || r.db == nil {
		return fmt.Errorf("mark ppt worker done: repository is unavailable")
	}
	runID = normalizeTaskID(runID)
	if runID == "" {
		return ErrTaskNotFound
	}
	finishedAt = normalizedTime(finishedAt)
	result, err := r.db.ExecContext(
		ctx,
		`UPDATE ppt_runs SET worker_active = 0, worker_finished_at = ? WHERE run_id = ? AND worker_active = 1 AND status IN ('succeeded', 'failed', 'cancelled')`,
		finishedAt,
		runID,
	)
	if err != nil {
		return fmt.Errorf("mark ppt worker done: %w", err)
	}
	return r.requireTerminalRunAfterNoop(ctx, runID, result)
}

func (r *mysqlRunRepository) TouchHeartbeat(ctx context.Context, runID string, heartbeatAt time.Time) error {
	if r == nil || r.db == nil {
		return fmt.Errorf("touch ppt worker heartbeat: repository is unavailable")
	}
	runID = normalizeTaskID(runID)
	if runID == "" {
		return ErrTaskNotFound
	}
	heartbeatAt = normalizedTime(heartbeatAt)
	result, err := r.db.ExecContext(
		ctx,
		`UPDATE ppt_runs SET heartbeat_at = ? WHERE run_id = ? AND worker_active = 1`,
		heartbeatAt,
		runID,
	)
	if err != nil {
		return fmt.Errorf("touch ppt worker heartbeat: %w", err)
	}
	return r.requireRunAfterNoop(ctx, runID, result)
}

// RecoverInterrupted is the single-process restart policy. Every worker left
// active by the previous API process is released. A still-running run receives
// a durable SERVER_RESTARTED terminal event; an already-terminal run only has
// its worker guard released.
func (r *mysqlRunRepository) RecoverInterrupted(ctx context.Context, recoveredAt time.Time) (int, error) {
	if r == nil || r.db == nil {
		return 0, fmt.Errorf("recover interrupted ppt runs: repository is unavailable")
	}
	recoveredAt = normalizedTime(recoveredAt)
	tx, err := r.db.BeginTx(ctx, nil)
	if err != nil {
		return 0, fmt.Errorf("begin interrupted ppt run recovery: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	rows, err := tx.QueryContext(ctx, selectRunColumns+` WHERE worker_active = 1 FOR UPDATE`)
	if err != nil {
		return 0, fmt.Errorf("lock interrupted ppt runs: %w", err)
	}
	interrupted := make([]persistedRun, 0)
	for rows.Next() {
		stored, scanErr := scanPersistedRun(rows)
		if scanErr != nil {
			_ = rows.Close()
			return 0, fmt.Errorf("scan interrupted ppt run: %w", scanErr)
		}
		interrupted = append(interrupted, stored)
	}
	if err := rows.Err(); err != nil {
		_ = rows.Close()
		return 0, fmt.Errorf("iterate interrupted ppt runs: %w", err)
	}
	if err := rows.Close(); err != nil {
		return 0, fmt.Errorf("close interrupted ppt runs: %w", err)
	}

	for _, stored := range interrupted {
		previous := stored.task
		if previous.Status.Terminal() {
			result, updateErr := tx.ExecContext(
				ctx,
				`UPDATE ppt_runs SET worker_active = 0, worker_finished_at = ? WHERE run_id = ? AND worker_active = 1`,
				recoveredAt,
				previous.RunID,
			)
			if updateErr != nil {
				return 0, fmt.Errorf("release terminal ppt worker: %w", updateErr)
			}
			if err := requireOneAffected(result); err != nil {
				return 0, err
			}
			continue
		}

		next, canonical, reduceErr := reduceTaskEvent(previous, AgentEvent{
			RunID:     previous.RunID,
			TaskID:    previous.RunID,
			Sequence:  previous.LastEventSequence + 1,
			CreatedAt: recoveredAt,
			Type:      EventTaskFailed,
			Stage:     previous.Stage,
			Message:   "PPT task was interrupted by an API restart",
			Error: &ErrorDetail{
				Code:      "SERVER_RESTARTED",
				Message:   "PPT generation was interrupted by a service restart",
				Retryable: true,
			},
		}, recoveredAt)
		if reduceErr != nil {
			return 0, fmt.Errorf("reduce interrupted ppt run %s: %w", previous.RunID, reduceErr)
		}
		if err := insertCanonicalEvent(ctx, tx, canonical); err != nil {
			return 0, err
		}
		if err := updateRunSnapshot(ctx, tx, previous, next, true, recoveredAt); err != nil {
			return 0, err
		}
	}

	if err := tx.Commit(); err != nil {
		return 0, fmt.Errorf("commit interrupted ppt run recovery: %w", err)
	}
	return len(interrupted), nil
}

func (r *mysqlRunRepository) findActiveThread(ctx context.Context, ownerID int64, threadID string) (*Task, error) {
	stored, err := scanPersistedRun(r.db.QueryRowContext(
		ctx,
		selectRunColumns+` WHERE owner_id = ? AND thread_id = ? AND worker_active = 1 LIMIT 1`,
		ownerID,
		threadID,
	))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("find active ppt thread: %w", err)
	}
	return cloneTask(&stored.task), nil
}

func (r *mysqlRunRepository) requireRunAfterNoop(ctx context.Context, runID string, result sql.Result) error {
	affected, err := result.RowsAffected()
	if err != nil {
		return fmt.Errorf("read affected ppt run rows: %w", err)
	}
	if affected > 0 {
		return nil
	}
	var found int
	if err := r.db.QueryRowContext(ctx, `SELECT 1 FROM ppt_runs WHERE run_id = ? LIMIT 1`, runID).Scan(&found); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return ErrTaskNotFound
		}
		return fmt.Errorf("check ppt run: %w", err)
	}
	return nil
}

func (r *mysqlRunRepository) requireTerminalRunAfterNoop(ctx context.Context, runID string, result sql.Result) error {
	affected, err := result.RowsAffected()
	if err != nil {
		return fmt.Errorf("read affected ppt run rows: %w", err)
	}
	if affected > 0 {
		return nil
	}
	var status TaskStatus
	if err := r.db.QueryRowContext(ctx, `SELECT status FROM ppt_runs WHERE run_id = ? LIMIT 1`, runID).Scan(&status); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return ErrTaskNotFound
		}
		return fmt.Errorf("check ppt run terminal status: %w", err)
	}
	if !status.Terminal() {
		return fmt.Errorf("%w: cannot release a running ppt worker", ErrInvalidRequest)
	}
	return nil
}

func prepareRunForCreate(task Task, fingerprint string) (persistedRun, error) {
	runID := normalizeTaskID(task.RunID)
	if runID == "" {
		runID = normalizeTaskID(task.TaskID)
	}
	if task.RunID != "" && task.TaskID != "" && normalizeTaskID(task.RunID) != normalizeTaskID(task.TaskID) {
		return persistedRun{}, fmt.Errorf("%w: task_id and run_id do not match", ErrInvalidRequest)
	}
	task.RunID = runID
	task.TaskID = runID
	task.ThreadID = strings.TrimSpace(task.ThreadID)
	task.ClientRequestID = strings.TrimSpace(task.ClientRequestID)
	task.Name = strings.TrimSpace(task.Name)
	task.Description = strings.TrimSpace(task.Description)
	fingerprint = strings.TrimSpace(fingerprint)
	if task.ownerID <= 0 || task.RunID == "" || task.ThreadID == "" || task.Description == "" {
		return persistedRun{}, fmt.Errorf("%w: run, thread, owner, and description are required", ErrInvalidRequest)
	}
	if task.ClientRequestID != "" && fingerprint == "" {
		return persistedRun{}, fmt.Errorf("%w: request fingerprint is required with client_request_id", ErrInvalidRequest)
	}
	if task.ClientRequestID == "" {
		fingerprint = ""
	}
	if task.SchemaVersion == "" {
		task.SchemaVersion = CurrentSchemaVersion
	}
	if task.Status == "" {
		task.Status = TaskRunning
	}
	if task.Stage == "" {
		task.Stage = StagePreparing
	}
	if task.Message == "" {
		task.Message = "PPT task created"
	}
	task.CreatedAt = normalizedTime(task.CreatedAt)
	if task.UpdatedAt.IsZero() {
		task.UpdatedAt = task.CreatedAt
	} else {
		task.UpdatedAt = normalizedTime(task.UpdatedAt)
	}
	return persistedRun{task: task, requestFingerprint: fingerprint, workerActive: true}, nil
}

func scanPersistedRun(scanner runScanner) (persistedRun, error) {
	var stored persistedRun
	var clientRequestID sql.NullString
	var requestFingerprint sql.NullString
	var agentConfigID sql.NullInt64
	var eventID sql.NullString
	var status string
	var stage string
	var resultJSON []byte
	var errorJSON []byte
	var heartbeatAt time.Time
	var finishedAt sql.NullTime
	var workerFinishedAt sql.NullTime

	err := scanner.Scan(
		&stored.task.RunID,
		&stored.task.ownerID,
		&stored.task.ThreadID,
		&clientRequestID,
		&requestFingerprint,
		&agentConfigID,
		&stored.task.Name,
		&stored.task.Description,
		&stored.task.SchemaVersion,
		&eventID,
		&status,
		&stage,
		&stored.task.Message,
		&stored.task.LastEventSequence,
		&resultJSON,
		&errorJSON,
		&stored.task.FileURL,
		&stored.workerActive,
		&heartbeatAt,
		&stored.task.CreatedAt,
		&stored.task.UpdatedAt,
		&finishedAt,
		&workerFinishedAt,
	)
	if err != nil {
		return persistedRun{}, err
	}
	stored.task.TaskID = stored.task.RunID
	stored.task.ClientRequestID = clientRequestID.String
	stored.requestFingerprint = requestFingerprint.String
	stored.task.AgentConfigID = agentConfigID.Int64
	if stored.task.Name == "" {
		stored.task.Name, _ = normalizePresentationName("", stored.task.Description)
	}
	stored.task.EventID = eventID.String
	stored.task.Status = TaskStatus(status)
	stored.task.Stage = Stage(stage)
	// go-sql-driver converts bound time.Time values into the configured DSN
	// location before writing DATETIME and parses them back in that same
	// location. Normalize the recovered instant to UTC for the public API.
	stored.task.CreatedAt = stored.task.CreatedAt.UTC()
	stored.task.UpdatedAt = stored.task.UpdatedAt.UTC()
	if finishedAt.Valid {
		value := finishedAt.Time.UTC()
		stored.task.FinishedAt = &value
	}
	if len(resultJSON) > 0 {
		if err := json.Unmarshal(resultJSON, &stored.task.Result); err != nil {
			return persistedRun{}, fmt.Errorf("decode ppt run result: %w", err)
		}
	}
	if len(errorJSON) > 0 {
		if err := json.Unmarshal(errorJSON, &stored.task.Error); err != nil {
			return persistedRun{}, fmt.Errorf("decode ppt run error: %w", err)
		}
	}
	return stored, nil
}

func insertCanonicalEvent(ctx context.Context, tx *sql.Tx, event AgentEvent) error {
	payload, err := json.Marshal(event)
	if err != nil {
		return fmt.Errorf("marshal ppt run event: %w", err)
	}
	_, err = tx.ExecContext(
		ctx,
		insertRunEventQuery,
		event.RunID,
		event.Sequence,
		event.EventID,
		event.SchemaVersion,
		event.Type,
		event.Channel,
		event.Stage,
		payload,
		event.CreatedAt,
	)
	if err != nil {
		if isMySQLDuplicate(err) {
			return fmt.Errorf("%w: duplicate event id or sequence", ErrInvalidRequest)
		}
		return fmt.Errorf("insert ppt run event: %w", err)
	}
	return nil
}

func updateRunSnapshot(ctx context.Context, tx *sql.Tx, previous, next Task, workerDone bool, workerFinishedAt time.Time) error {
	resultJSON, err := marshalOptionalJSON(next.Result)
	if err != nil {
		return fmt.Errorf("marshal ppt run result: %w", err)
	}
	errorJSON, err := marshalOptionalJSON(next.Error)
	if err != nil {
		return fmt.Errorf("marshal ppt run error: %w", err)
	}
	query := updateRunSnapshotQuery
	args := []any{
		next.SchemaVersion,
		nullableString(next.EventID),
		next.Status,
		next.Stage,
		next.Message,
		next.LastEventSequence,
		resultJSON,
		errorJSON,
		next.FileURL,
		next.UpdatedAt,
		next.UpdatedAt,
		next.FinishedAt,
	}
	if workerDone {
		query = updateRecoveredRunSnapshotQuery
		args = append(args, normalizedTime(workerFinishedAt))
	}
	args = append(args, previous.RunID, previous.LastEventSequence, previous.Status)
	result, err := tx.ExecContext(ctx, query, args...)
	if err != nil {
		return fmt.Errorf("update ppt run snapshot: %w", err)
	}
	if err := requireOneAffected(result); err != nil {
		return fmt.Errorf("update ppt run snapshot: %w", err)
	}
	return nil
}

func requireOneAffected(result sql.Result) error {
	affected, err := result.RowsAffected()
	if err != nil {
		return fmt.Errorf("read affected rows: %w", err)
	}
	if affected != 1 {
		return fmt.Errorf("%w: concurrent ppt run transition", ErrInvalidRequest)
	}
	return nil
}

func marshalOptionalJSON(value any) (any, error) {
	if value == nil {
		return nil, nil
	}
	reflected := reflect.ValueOf(value)
	switch reflected.Kind() {
	case reflect.Chan, reflect.Func, reflect.Interface, reflect.Map, reflect.Pointer, reflect.Slice:
		if reflected.IsNil() {
			return nil, nil
		}
	}
	payload, err := json.Marshal(value)
	if err != nil {
		return nil, err
	}
	return payload, nil
}

func nullableString(value string) any {
	if strings.TrimSpace(value) == "" {
		return nil
	}
	return value
}

func nullablePositiveInt64(value int64) any {
	if value <= 0 {
		return nil
	}
	return value
}

func normalizedTime(value time.Time) time.Time {
	if value.IsZero() {
		value = time.Now()
	}
	return value.UTC().Truncate(time.Microsecond)
}

func isMySQLDuplicate(err error) bool {
	var mysqlErr *mysqlDriver.MySQLError
	return errors.As(err, &mysqlErr) && mysqlErr.Number == 1062
}
