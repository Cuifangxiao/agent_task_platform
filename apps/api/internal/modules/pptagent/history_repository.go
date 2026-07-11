package pptagent

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
)

var ErrConversationNotFound = errors.New("ppt conversation not found")
var ErrConversationActive = errors.New("ppt conversation has an active job")

type HistoryRepository interface {
	CreateConversation(ctx context.Context, conversation Conversation) (*Conversation, error)
	GetConversationByID(ctx context.Context, userID int64, id int64) (*Conversation, error)
	GetConversationByRequestID(ctx context.Context, userID int64, requestID string) (*Conversation, error)
	ListConversationsByUserID(ctx context.Context, userID int64) ([]*Conversation, error)
	DeleteConversation(ctx context.Context, userID int64, id int64) ([]string, error)
	CreateMessage(ctx context.Context, message ConversationMessage) (*ConversationMessage, error)
	UpdateMessageContent(ctx context.Context, messageID int64, requestID string, contentJSON string) error
	GetLatestMessageByKindAndRequestID(ctx context.Context, conversationID int64, kind string, requestID string) (*ConversationMessage, error)
	ListMessagesByConversationID(ctx context.Context, userID int64, conversationID int64) ([]*ConversationMessage, error)
	UpdateConversationState(ctx context.Context, conversationID int64, status ConversationStatus, latestRequestID string) error
}

type MySQLHistoryRepository struct {
	db *sql.DB
}

func NewHistoryRepository(db *sql.DB) *MySQLHistoryRepository {
	return &MySQLHistoryRepository{db: db}
}

func (r *MySQLHistoryRepository) CreateConversation(ctx context.Context, conversation Conversation) (*Conversation, error) {
	result, err := r.db.ExecContext(
		ctx,
		`
			INSERT INTO ppt_chat_conversations (
				user_id, agent_config_id, agent_config_name, title, status, latest_request_id
			)
			VALUES (?, ?, ?, ?, ?, ?)
		`,
		conversation.UserID,
		conversation.AgentConfigID,
		conversation.AgentConfigName,
		conversation.Title,
		conversation.Status,
		conversation.LatestRequestID,
	)
	if err != nil {
		return nil, fmt.Errorf("insert ppt conversation: %w", err)
	}

	id, err := result.LastInsertId()
	if err != nil {
		return nil, fmt.Errorf("read inserted ppt conversation id: %w", err)
	}

	return r.GetConversationByID(ctx, conversation.UserID, id)
}

func (r *MySQLHistoryRepository) GetConversationByID(ctx context.Context, userID int64, id int64) (*Conversation, error) {
	row := r.db.QueryRowContext(
		ctx,
		`
			SELECT
				id, user_id, agent_config_id, agent_config_name, title, status, latest_request_id, created_at, updated_at
			FROM ppt_chat_conversations
			WHERE user_id = ? AND id = ?
			LIMIT 1
		`,
		userID,
		id,
	)

	conversation, err := scanConversation(row)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, ErrConversationNotFound
		}
		return nil, fmt.Errorf("query ppt conversation: %w", err)
	}
	return conversation, nil
}

func (r *MySQLHistoryRepository) GetConversationByRequestID(ctx context.Context, userID int64, requestID string) (*Conversation, error) {
	row := r.db.QueryRowContext(
		ctx,
		`
			SELECT
				id, user_id, agent_config_id, agent_config_name, title, status, latest_request_id, created_at, updated_at
			FROM ppt_chat_conversations
			WHERE user_id = ? AND latest_request_id = ?
			ORDER BY id DESC
			LIMIT 1
		`,
		userID,
		requestID,
	)

	conversation, err := scanConversation(row)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, ErrConversationNotFound
		}
		return nil, fmt.Errorf("query ppt conversation by request id: %w", err)
	}
	return conversation, nil
}

func (r *MySQLHistoryRepository) ListConversationsByUserID(ctx context.Context, userID int64) ([]*Conversation, error) {
	rows, err := r.db.QueryContext(
		ctx,
		`
			SELECT
				id, user_id, agent_config_id, agent_config_name, title, status, latest_request_id, created_at, updated_at
			FROM ppt_chat_conversations
			WHERE user_id = ?
			ORDER BY updated_at DESC, id DESC
		`,
		userID,
	)
	if err != nil {
		return nil, fmt.Errorf("query ppt conversations: %w", err)
	}
	defer rows.Close()

	conversations := make([]*Conversation, 0)
	for rows.Next() {
		conversation, err := scanConversation(rows)
		if err != nil {
			return nil, fmt.Errorf("scan ppt conversation: %w", err)
		}
		conversations = append(conversations, conversation)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate ppt conversations: %w", err)
	}

	return conversations, nil
}

func (r *MySQLHistoryRepository) DeleteConversation(ctx context.Context, userID int64, id int64) ([]string, error) {
	tx, err := r.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("begin delete ppt conversation: %w", err)
	}
	defer tx.Rollback()

	var latestRequestID string
	if err := tx.QueryRowContext(
		ctx,
		`SELECT latest_request_id FROM ppt_chat_conversations WHERE user_id = ? AND id = ? FOR UPDATE`,
		userID,
		id,
	).Scan(&latestRequestID); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, ErrConversationNotFound
		}
		return nil, fmt.Errorf("lock ppt conversation for deletion: %w", err)
	}

	requestIDs := make([]string, 0, 2)
	seenRequestIDs := make(map[string]struct{})
	appendRequestID := func(value string) {
		value = strings.TrimSpace(value)
		if value == "" {
			return
		}
		if _, exists := seenRequestIDs[value]; exists {
			return
		}
		seenRequestIDs[value] = struct{}{}
		requestIDs = append(requestIDs, value)
	}
	appendRequestID(latestRequestID)

	rows, err := tx.QueryContext(
		ctx,
		`SELECT request_id, status FROM ppt_deck_jobs WHERE user_id = ? AND conversation_id = ? FOR UPDATE`,
		userID,
		id,
	)
	if err != nil {
		return nil, fmt.Errorf("query ppt conversation jobs for deletion: %w", err)
	}
	for rows.Next() {
		var requestID sql.NullString
		var status DeckJobStatus
		if err := rows.Scan(&requestID, &status); err != nil {
			rows.Close()
			return nil, fmt.Errorf("scan ppt conversation job for deletion: %w", err)
		}
		if conversationDeletionBlocked(status) {
			rows.Close()
			return nil, ErrConversationActive
		}
		if requestID.Valid {
			appendRequestID(requestID.String)
		}
	}
	if err := rows.Err(); err != nil {
		rows.Close()
		return nil, fmt.Errorf("iterate ppt conversation jobs for deletion: %w", err)
	}
	if err := rows.Close(); err != nil {
		return nil, fmt.Errorf("close ppt conversation jobs for deletion: %w", err)
	}

	result, err := tx.ExecContext(
		ctx,
		`DELETE FROM ppt_chat_conversations WHERE user_id = ? AND id = ?`,
		userID,
		id,
	)
	if err != nil {
		return nil, fmt.Errorf("delete ppt conversation: %w", err)
	}
	if affected, err := result.RowsAffected(); err != nil {
		return nil, fmt.Errorf("read deleted ppt conversation count: %w", err)
	} else if affected != 1 {
		return nil, ErrConversationNotFound
	}

	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("commit delete ppt conversation: %w", err)
	}
	return requestIDs, nil
}

func conversationDeletionBlocked(status DeckJobStatus) bool {
	return !status.Terminal() && !status.Waiting()
}

func (r *MySQLHistoryRepository) CreateMessage(ctx context.Context, message ConversationMessage) (*ConversationMessage, error) {
	result, err := r.db.ExecContext(
		ctx,
		`
			INSERT INTO ppt_chat_messages (
				conversation_id, role, kind, request_id, content_json
			)
			VALUES (?, ?, ?, ?, ?)
		`,
		message.ConversationID,
		message.Role,
		message.Kind,
		message.RequestID,
		message.ContentJSON,
	)
	if err != nil {
		return nil, fmt.Errorf("insert ppt message: %w", err)
	}

	id, err := result.LastInsertId()
	if err != nil {
		return nil, fmt.Errorf("read inserted ppt message id: %w", err)
	}

	row := r.db.QueryRowContext(
		ctx,
		`
			SELECT
				id, conversation_id, role, kind, request_id, content_json, created_at, updated_at
			FROM ppt_chat_messages
			WHERE id = ?
			LIMIT 1
		`,
		id,
	)
	messageRow, err := scanConversationMessage(row)
	if err != nil {
		return nil, fmt.Errorf("query inserted ppt message: %w", err)
	}

	return messageRow, nil
}

func (r *MySQLHistoryRepository) UpdateMessageContent(ctx context.Context, messageID int64, requestID string, contentJSON string) error {
	_, err := r.db.ExecContext(
		ctx,
		`
			UPDATE ppt_chat_messages
			SET request_id = ?, content_json = ?
			WHERE id = ?
		`,
		requestID,
		contentJSON,
		messageID,
	)
	if err != nil {
		return fmt.Errorf("update ppt message: %w", err)
	}
	return nil
}

func (r *MySQLHistoryRepository) GetLatestMessageByKindAndRequestID(
	ctx context.Context,
	conversationID int64,
	kind string,
	requestID string,
) (*ConversationMessage, error) {
	row := r.db.QueryRowContext(
		ctx,
		`
			SELECT
				id, conversation_id, role, kind, request_id, content_json, created_at, updated_at
			FROM ppt_chat_messages
			WHERE conversation_id = ? AND kind = ? AND request_id = ?
			ORDER BY id DESC
			LIMIT 1
		`,
		conversationID,
		kind,
		requestID,
	)

	message, err := scanConversationMessage(row)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, ErrConversationNotFound
		}
		return nil, fmt.Errorf("query ppt message by request/kind: %w", err)
	}
	return message, nil
}

func (r *MySQLHistoryRepository) ListMessagesByConversationID(ctx context.Context, userID int64, conversationID int64) ([]*ConversationMessage, error) {
	rows, err := r.db.QueryContext(
		ctx,
		`
			SELECT
				m.id, m.conversation_id, m.role, m.kind, m.request_id, m.content_json, m.created_at, m.updated_at
			FROM ppt_chat_messages m
			INNER JOIN ppt_chat_conversations c ON c.id = m.conversation_id
			WHERE c.user_id = ? AND m.conversation_id = ?
			ORDER BY m.id ASC
		`,
		userID,
		conversationID,
	)
	if err != nil {
		return nil, fmt.Errorf("query ppt messages: %w", err)
	}
	defer rows.Close()

	messages := make([]*ConversationMessage, 0)
	for rows.Next() {
		message, err := scanConversationMessage(rows)
		if err != nil {
			return nil, fmt.Errorf("scan ppt message: %w", err)
		}
		messages = append(messages, message)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate ppt messages: %w", err)
	}

	return messages, nil
}

func (r *MySQLHistoryRepository) UpdateConversationState(
	ctx context.Context,
	conversationID int64,
	status ConversationStatus,
	latestRequestID string,
) error {
	_, err := r.db.ExecContext(
		ctx,
		`
			UPDATE ppt_chat_conversations
			SET status = ?, latest_request_id = ?
			WHERE id = ?
		`,
		status,
		latestRequestID,
		conversationID,
	)
	if err != nil {
		return fmt.Errorf("update ppt conversation state: %w", err)
	}
	return nil
}

func scanConversation(src interface{ Scan(dest ...any) error }) (*Conversation, error) {
	var conversation Conversation
	if err := src.Scan(
		&conversation.ID,
		&conversation.UserID,
		&conversation.AgentConfigID,
		&conversation.AgentConfigName,
		&conversation.Title,
		&conversation.Status,
		&conversation.LatestRequestID,
		&conversation.CreatedAt,
		&conversation.UpdatedAt,
	); err != nil {
		return nil, err
	}
	return &conversation, nil
}

func scanConversationMessage(src interface{ Scan(dest ...any) error }) (*ConversationMessage, error) {
	var message ConversationMessage
	if err := src.Scan(
		&message.ID,
		&message.ConversationID,
		&message.Role,
		&message.Kind,
		&message.RequestID,
		&message.ContentJSON,
		&message.CreatedAt,
		&message.UpdatedAt,
	); err != nil {
		return nil, err
	}
	return &message, nil
}
