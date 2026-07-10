package pptagent

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
)

var ErrDeckJobNotFound = errors.New("ppt deck job not found")

type JobRepository interface {
	CreateJob(ctx context.Context, job DeckJob, sources []DeckJobSource) (*DeckJob, error)
	GetJobByID(ctx context.Context, userID int64, jobID string) (*DeckJob, error)
	GetJobByRequestID(ctx context.Context, userID int64, requestID string) (*DeckJob, error)
	GetJobForWorker(ctx context.Context, jobID string) (*DeckJob, error)
	GetLatestJobByConversation(ctx context.Context, userID int64, conversationID int64) (*DeckJob, error)
	ListRecoverableJobs(ctx context.Context) ([]*DeckJob, error)
	ListSources(ctx context.Context, jobID string) ([]DeckJobSource, error)
	UpdateJob(ctx context.Context, jobID string, status DeckJobStatus, stage, requestID, errorCode, errorSummary string, retryable bool) error
	UpdateJobQuality(ctx context.Context, jobID string, qualityJSON string) error
	RequestCancel(ctx context.Context, userID int64, jobID string) (*DeckJob, error)
	AppendEvent(ctx context.Context, event DeckJobEvent) (*DeckJobEvent, error)
	ListEvents(ctx context.Context, userID int64, jobID string, afterSequence int64, limit int) ([]DeckJobEvent, error)
	ActionExists(ctx context.Context, userID int64, jobID, idempotencyKey string) (bool, error)
	CreateAction(ctx context.Context, userID int64, jobID, idempotencyKey, action, payloadJSON string) (bool, error)
	UpdateActionStatus(ctx context.Context, jobID, idempotencyKey, status string) error
	UpsertAsset(ctx context.Context, asset DeckJobAsset) error
}

type MySQLJobRepository struct {
	db *sql.DB
}

type JobMetricsRepository interface {
	ProductMetrics(ctx context.Context, userID int64) (*DeckProductMetrics, error)
}

func NewJobRepository(db *sql.DB) *MySQLJobRepository {
	return &MySQLJobRepository{db: db}
}

func (r *MySQLJobRepository) CreateJob(ctx context.Context, job DeckJob, sources []DeckJobSource) (*DeckJob, error) {
	tx, err := r.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("begin create deck job: %w", err)
	}
	defer tx.Rollback()

	_, err = tx.ExecContext(ctx, `
		INSERT INTO ppt_deck_jobs (
			id, user_id, conversation_id, agent_config_id, mode, status, stage, input_json, quality_json
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
	`, job.ID, job.UserID, job.ConversationID, job.AgentConfigID, job.Mode, job.Status, job.Stage, job.InputJSON, normalizedJSONObject(job.QualityJSON))
	if err != nil {
		return nil, fmt.Errorf("insert deck job: %w", err)
	}
	for _, source := range sources {
		_, err = tx.ExecContext(ctx, `
			INSERT INTO ppt_deck_job_sources (
				job_id, filename, content, resource_id, relative_path, sha256, size_bytes
			) VALUES (?, ?, ?, ?, ?, ?, ?)
		`, job.ID, source.FileName, source.Content, source.ResourceID, source.RelativePath, source.SHA256, source.SizeBytes)
		if err != nil {
			return nil, fmt.Errorf("insert deck job source: %w", err)
		}
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("commit create deck job: %w", err)
	}
	return r.GetJobByID(ctx, job.UserID, job.ID)
}

func (r *MySQLJobRepository) GetJobByID(ctx context.Context, userID int64, jobID string) (*DeckJob, error) {
	return scanDeckJob(r.db.QueryRowContext(ctx, deckJobSelect+` WHERE user_id = ? AND id = ? LIMIT 1`, userID, jobID))
}

func (r *MySQLJobRepository) GetJobByRequestID(ctx context.Context, userID int64, requestID string) (*DeckJob, error) {
	return scanDeckJob(r.db.QueryRowContext(ctx, deckJobSelect+` WHERE user_id = ? AND request_id = ? LIMIT 1`, userID, requestID))
}

func (r *MySQLJobRepository) GetJobForWorker(ctx context.Context, jobID string) (*DeckJob, error) {
	return scanDeckJob(r.db.QueryRowContext(ctx, deckJobSelect+` WHERE id = ? LIMIT 1`, jobID))
}

func (r *MySQLJobRepository) GetLatestJobByConversation(ctx context.Context, userID int64, conversationID int64) (*DeckJob, error) {
	return scanDeckJob(r.db.QueryRowContext(ctx, deckJobSelect+` WHERE user_id = ? AND conversation_id = ? ORDER BY created_at DESC LIMIT 1`, userID, conversationID))
}

func (r *MySQLJobRepository) ListRecoverableJobs(ctx context.Context) ([]*DeckJob, error) {
	rows, err := r.db.QueryContext(ctx, deckJobSelect+` WHERE status IN ('queued', 'running', 'exporting') ORDER BY created_at ASC`)
	if err != nil {
		return nil, fmt.Errorf("query recoverable deck jobs: %w", err)
	}
	defer rows.Close()
	var result []*DeckJob
	for rows.Next() {
		job, err := scanDeckJob(rows)
		if err != nil {
			return nil, err
		}
		result = append(result, job)
	}
	return result, rows.Err()
}

func (r *MySQLJobRepository) ListSources(ctx context.Context, jobID string) ([]DeckJobSource, error) {
	rows, err := r.db.QueryContext(ctx, `
		SELECT filename, content, resource_id, relative_path, sha256, size_bytes
		FROM ppt_deck_job_sources WHERE job_id = ? ORDER BY id
	`, jobID)
	if err != nil {
		return nil, fmt.Errorf("query deck job sources: %w", err)
	}
	defer rows.Close()
	var result []DeckJobSource
	for rows.Next() {
		var source DeckJobSource
		if err := rows.Scan(&source.FileName, &source.Content, &source.ResourceID, &source.RelativePath, &source.SHA256, &source.SizeBytes); err != nil {
			return nil, fmt.Errorf("scan deck job source: %w", err)
		}
		result = append(result, source)
	}
	return result, rows.Err()
}

func (r *MySQLJobRepository) UpdateJob(ctx context.Context, jobID string, status DeckJobStatus, stage, requestID, errorCode, errorSummary string, retryable bool) error {
	finished := status.Terminal()
	_, err := r.db.ExecContext(ctx, `
		UPDATE ppt_deck_jobs
		SET status = ?, stage = ?, request_id = IF(? = '', request_id, ?),
			error_code = ?, error_summary = ?, retryable = ?,
			started_at = IF(started_at IS NULL AND ? IN ('running', 'exporting'), CURRENT_TIMESTAMP, started_at),
			finished_at = IF(?, CURRENT_TIMESTAMP, NULL)
		WHERE id = ?
	`, status, stage, requestID, requestID, errorCode, errorSummary, retryable, status, finished, jobID)
	if err != nil {
		return fmt.Errorf("update deck job: %w", err)
	}
	return nil
}

func (r *MySQLJobRepository) UpdateJobQuality(ctx context.Context, jobID string, qualityJSON string) error {
	_, err := r.db.ExecContext(ctx, `UPDATE ppt_deck_jobs SET quality_json = ? WHERE id = ?`, normalizedJSONObject(qualityJSON), jobID)
	if err != nil {
		return fmt.Errorf("update deck job quality: %w", err)
	}
	return nil
}

func (r *MySQLJobRepository) RequestCancel(ctx context.Context, userID int64, jobID string) (*DeckJob, error) {
	result, err := r.db.ExecContext(ctx, `
		UPDATE ppt_deck_jobs
		SET cancel_requested = TRUE, status = 'cancelled', stage = IF(stage = '', 'brief', stage), finished_at = CURRENT_TIMESTAMP
		WHERE id = ? AND user_id = ? AND status NOT IN ('success', 'success_with_warnings', 'needs_review', 'failed', 'cancelled')
	`, jobID, userID)
	if err != nil {
		return nil, fmt.Errorf("cancel deck job: %w", err)
	}
	if affected, _ := result.RowsAffected(); affected == 0 {
		job, getErr := r.GetJobByID(ctx, userID, jobID)
		if getErr != nil {
			return nil, getErr
		}
		return job, nil
	}
	return r.GetJobByID(ctx, userID, jobID)
}

func (r *MySQLJobRepository) AppendEvent(ctx context.Context, event DeckJobEvent) (*DeckJobEvent, error) {
	tx, err := r.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("begin append deck event: %w", err)
	}
	defer tx.Rollback()

	var last int64
	if err := tx.QueryRowContext(ctx, `SELECT last_event_sequence FROM ppt_deck_jobs WHERE id = ? FOR UPDATE`, event.JobID).Scan(&last); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, ErrDeckJobNotFound
		}
		return nil, fmt.Errorf("lock deck job sequence: %w", err)
	}
	event.Sequence = last + 1
	if event.EventVersion == 0 {
		event.EventVersion = DeckEventVersion
	}
	if event.Severity == "" {
		event.Severity = "info"
	}
	payload := normalizedJSON(event.Payload)
	result, err := tx.ExecContext(ctx, `
		INSERT INTO ppt_deck_job_events (
			job_id, sequence, event_version, request_id, conversation_id, event_type, stage, stage_status,
			message, progress_current, progress_total, slide_index, slide_version, severity,
			action_required, retryable, error_code, payload_json
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULLIF(?, ''), ?, ?, ?)
	`, event.JobID, event.Sequence, event.EventVersion, event.RequestID, event.ConversationID, event.EventType, event.Stage,
		event.StageStatus, event.Message, event.ProgressCurrent, event.ProgressTotal, event.SlideIndex, event.SlideVersion,
		event.Severity, event.ActionRequired, event.Retryable, event.ErrorCode, payload)
	if err != nil {
		return nil, fmt.Errorf("insert deck job event: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `UPDATE ppt_deck_jobs SET last_event_sequence = ? WHERE id = ?`, event.Sequence, event.JobID); err != nil {
		return nil, fmt.Errorf("advance deck job sequence: %w", err)
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("commit deck job event: %w", err)
	}
	event.ID, _ = result.LastInsertId()
	event.Payload = []byte(payload)
	return &event, nil
}

func (r *MySQLJobRepository) ListEvents(ctx context.Context, userID int64, jobID string, afterSequence int64, limit int) ([]DeckJobEvent, error) {
	if limit <= 0 || limit > 500 {
		limit = 200
	}
	rows, err := r.db.QueryContext(ctx, `
		SELECT e.id, e.event_version, e.job_id, e.request_id, e.conversation_id, e.sequence,
			e.event_type, e.stage, e.stage_status, e.message, e.progress_current, e.progress_total,
			e.slide_index, e.slide_version, e.severity, COALESCE(e.action_required, ''), e.retryable,
			e.error_code, e.payload_json, e.created_at
		FROM ppt_deck_job_events e
		INNER JOIN ppt_deck_jobs j ON j.id = e.job_id
		WHERE j.user_id = ? AND e.job_id = ? AND e.sequence > ?
		ORDER BY e.sequence ASC LIMIT ?
	`, userID, jobID, afterSequence, limit)
	if err != nil {
		return nil, fmt.Errorf("query deck job events: %w", err)
	}
	defer rows.Close()
	var result []DeckJobEvent
	for rows.Next() {
		var event DeckJobEvent
		if err := rows.Scan(&event.ID, &event.EventVersion, &event.JobID, &event.RequestID, &event.ConversationID,
			&event.Sequence, &event.EventType, &event.Stage, &event.StageStatus, &event.Message,
			&event.ProgressCurrent, &event.ProgressTotal, &event.SlideIndex, &event.SlideVersion,
			&event.Severity, &event.ActionRequired, &event.Retryable, &event.ErrorCode, &event.Payload, &event.CreatedAt); err != nil {
			return nil, fmt.Errorf("scan deck job event: %w", err)
		}
		event.PayloadValue = decodeJSONValue(event.Payload)
		result = append(result, event)
	}
	return result, rows.Err()
}

func (r *MySQLJobRepository) ActionExists(ctx context.Context, userID int64, jobID, idempotencyKey string) (bool, error) {
	var exists bool
	err := r.db.QueryRowContext(ctx, `
		SELECT EXISTS (
			SELECT 1
			FROM ppt_deck_job_actions AS action
			JOIN ppt_deck_jobs AS job ON job.id = action.job_id
			WHERE action.job_id = ? AND action.idempotency_key = ? AND job.user_id = ?
		)
	`, jobID, idempotencyKey, userID).Scan(&exists)
	if err != nil {
		return false, fmt.Errorf("query deck job action: %w", err)
	}
	return exists, nil
}

func (r *MySQLJobRepository) CreateAction(ctx context.Context, userID int64, jobID, idempotencyKey, action, payloadJSON string) (bool, error) {
	result, err := r.db.ExecContext(ctx, `
		INSERT IGNORE INTO ppt_deck_job_actions (job_id, idempotency_key, action, payload_json)
		SELECT id, ?, ?, ? FROM ppt_deck_jobs WHERE id = ? AND user_id = ?
	`, idempotencyKey, action, normalizedJSONObject(payloadJSON), jobID, userID)
	if err != nil {
		return false, fmt.Errorf("insert deck job action: %w", err)
	}
	affected, _ := result.RowsAffected()
	if affected > 0 {
		return true, nil
	}
	if _, err := r.GetJobByID(ctx, userID, jobID); err != nil {
		return false, err
	}
	return false, nil
}

func (r *MySQLJobRepository) UpdateActionStatus(ctx context.Context, jobID, idempotencyKey, status string) error {
	_, err := r.db.ExecContext(ctx, `UPDATE ppt_deck_job_actions SET status = ? WHERE job_id = ? AND idempotency_key = ?`, status, jobID, idempotencyKey)
	return err
}

func (r *MySQLJobRepository) UpsertAsset(ctx context.Context, asset DeckJobAsset) error {
	_, err := r.db.ExecContext(ctx, `
		INSERT INTO ppt_deck_job_assets (
			job_id, request_id, kind, slide_index, version, relative_path, quality_status, metadata_json
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
		ON DUPLICATE KEY UPDATE request_id = VALUES(request_id), relative_path = VALUES(relative_path),
			quality_status = VALUES(quality_status), metadata_json = VALUES(metadata_json)
	`, asset.JobID, asset.RequestID, asset.Kind, asset.SlideIndex, asset.Version, asset.RelativePath,
		asset.QualityStatus, normalizedJSONObject(asset.MetadataJSON))
	if err != nil {
		return fmt.Errorf("upsert deck job asset: %w", err)
	}
	return nil
}

func (r *MySQLJobRepository) ProductMetrics(ctx context.Context, userID int64) (*DeckProductMetrics, error) {
	var metrics DeckProductMetrics
	var repairedSuccessful, repairedTotal, downloads int64
	err := r.db.QueryRowContext(ctx, `
		SELECT
			COUNT(*),
			COALESCE(SUM(status IN ('success', 'success_with_warnings')), 0),
			COALESCE(SUM(status IN ('success', 'success_with_warnings', 'needs_review')), 0),
			COALESCE(AVG(CASE WHEN finished_at IS NOT NULL AND started_at IS NOT NULL THEN TIMESTAMPDIFF(MICROSECOND, started_at, finished_at) / 1000 END), 0),
			COUNT(DISTINCT CASE WHEN EXISTS (SELECT 1 FROM ppt_deck_job_events e WHERE e.job_id = j.id AND e.event_type = 'subscription_disconnected') THEN j.id END),
			COUNT(DISTINCT CASE WHEN EXISTS (SELECT 1 FROM ppt_deck_job_events e WHERE e.job_id = j.id AND e.event_type = 'subscription_resumed') THEN j.id END),
			COUNT(DISTINCT CASE WHEN EXISTS (SELECT 1 FROM ppt_deck_job_actions a WHERE a.job_id = j.id AND a.action = 'regenerate_slide') THEN j.id END),
			COUNT(DISTINCT CASE WHEN EXISTS (
				SELECT 1 FROM ppt_deck_job_events e WHERE e.job_id = j.id AND e.event_type = 'repair_progress'
				AND COALESCE(JSON_UNQUOTE(JSON_EXTRACT(e.payload_json, '$.kind')), '') <> 'user_feedback'
			) THEN j.id END),
			COUNT(DISTINCT CASE WHEN status IN ('success', 'success_with_warnings') AND EXISTS (
				SELECT 1 FROM ppt_deck_job_events e WHERE e.job_id = j.id AND e.event_type = 'repair_progress'
				AND COALESCE(JSON_UNQUOTE(JSON_EXTRACT(e.payload_json, '$.kind')), '') <> 'user_feedback'
			) THEN j.id END),
			COUNT(DISTINCT CASE WHEN EXISTS (SELECT 1 FROM ppt_deck_job_events e WHERE e.job_id = j.id AND e.event_type = 'artifact_downloaded') THEN j.id END)
		FROM ppt_deck_jobs j WHERE user_id = ?
	`, userID).Scan(&metrics.TotalJobs, &metrics.SuccessfulJobs, &metrics.ExportedJobs, &metrics.AverageGenerationMS,
		&metrics.DisconnectedJobs, &metrics.RecoveredSubscriptions, &metrics.RegeneratedJobs, &repairedTotal, &repairedSuccessful, &downloads)
	if err != nil {
		return nil, fmt.Errorf("query deck product metrics: %w", err)
	}
	if metrics.TotalJobs > 0 {
		metrics.SuccessRate = float64(metrics.SuccessfulJobs) / float64(metrics.TotalJobs)
		metrics.RegenerateRate = float64(metrics.RegeneratedJobs) / float64(metrics.TotalJobs)
		metrics.SlideRegenerateRate = metrics.RegenerateRate
	}
	if metrics.ExportedJobs > 0 {
		metrics.DownloadSuccessRate = float64(downloads) / float64(metrics.ExportedJobs)
	}
	if metrics.DisconnectedJobs > 0 {
		recovered := metrics.RecoveredSubscriptions
		if recovered > metrics.DisconnectedJobs {
			recovered = metrics.DisconnectedJobs
		}
		metrics.SubscriptionRecoveryRate = float64(recovered) / float64(metrics.DisconnectedJobs)
	}
	if repairedTotal > 0 {
		metrics.AutoRepairSuccessRate = float64(repairedSuccessful) / float64(repairedTotal)
	}
	rows, queryErr := r.db.QueryContext(ctx, `
		SELECT e.payload_json FROM ppt_deck_job_events e
		INNER JOIN ppt_deck_jobs j ON j.id = e.job_id
		WHERE j.user_id = ? AND e.event_type = 'artifact'
			AND e.sequence = (
				SELECT MAX(e2.sequence) FROM ppt_deck_job_events e2
				WHERE e2.job_id = e.job_id AND e2.event_type = 'artifact'
			)
	`, userID)
	if queryErr == nil {
		defer rows.Close()
		var reports, mustFixTotal int
		for rows.Next() {
			var raw []byte
			if rows.Scan(&raw) != nil {
				continue
			}
			var payload struct {
				Issues []struct {
					Severity string `json:"severity"`
				} `json:"issues"`
			}
			if json.Unmarshal(raw, &payload) != nil {
				continue
			}
			reports++
			for _, issue := range payload.Issues {
				if issue.Severity == "must_fix" {
					mustFixTotal++
				}
			}
		}
		if reports > 0 {
			metrics.AverageMustFixIssues = float64(mustFixTotal) / float64(reports)
		}
	}
	return &metrics, nil
}

const deckJobSelect = `
	SELECT id, user_id, conversation_id, agent_config_id, COALESCE(request_id, ''), mode, status, stage,
		error_code, error_summary, retryable, cancel_requested, last_event_sequence,
		input_json, quality_json, started_at, finished_at, created_at, updated_at
	FROM ppt_deck_jobs`

func scanDeckJob(src interface{ Scan(dest ...any) error }) (*DeckJob, error) {
	var job DeckJob
	var startedAt, finishedAt sql.NullTime
	if err := src.Scan(&job.ID, &job.UserID, &job.ConversationID, &job.AgentConfigID, &job.RequestID,
		&job.Mode, &job.Status, &job.Stage, &job.ErrorCode, &job.ErrorSummary, &job.Retryable,
		&job.CancelRequested, &job.LastEventSequence, &job.InputJSON, &job.QualityJSON,
		&startedAt, &finishedAt, &job.CreatedAt, &job.UpdatedAt); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, ErrDeckJobNotFound
		}
		return nil, fmt.Errorf("scan deck job: %w", err)
	}
	if startedAt.Valid {
		job.StartedAt = &startedAt.Time
	}
	if finishedAt.Valid {
		job.FinishedAt = &finishedAt.Time
	}
	return &job, nil
}

func normalizedJSONObject(raw string) string {
	return normalizedJSON([]byte(raw))
}

func normalizedJSON(raw []byte) string {
	if len(raw) == 0 || !json.Valid(raw) {
		return "{}"
	}
	trimmed := strings.TrimSpace(string(raw))
	if !strings.HasPrefix(trimmed, "{") && !strings.HasPrefix(trimmed, "[") {
		return "{}"
	}
	return trimmed
}

func decodeJSONValue(raw []byte) any {
	var value any
	if len(raw) == 0 || json.Unmarshal(raw, &value) != nil {
		return map[string]any{}
	}
	return value
}
