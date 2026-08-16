package agentconfig

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"

	mysqlDriver "github.com/go-sql-driver/mysql"
)

var ErrNameAlreadyExists = errors.New("agent config name already exists")

const selectAgentColumns = `
	SELECT id, agent_key, name, description, node_schema, status, created_at, updated_at
	FROM agents
`

const selectAgentConfigColumns = `
	SELECT
		c.id, c.user_id, c.agent_id, a.agent_key, a.name,
		c.name, c.description, c.is_default, c.status, c.created_at, c.updated_at
	FROM agent_configs c
	INNER JOIN agents a ON a.id = c.agent_id
`

type Repository interface {
	ListAgents(ctx context.Context) ([]*AgentDefinition, error)
	GetAgentByID(ctx context.Context, id int64) (*AgentDefinition, error)
	CreateConfig(ctx context.Context, config AgentConfig) (*AgentConfig, error)
	ListConfigsByUserID(ctx context.Context, userID int64, agentID int64) ([]*AgentConfig, error)
	GetConfigByID(ctx context.Context, userID int64, id int64) (*AgentConfig, error)
	UpdateConfig(ctx context.Context, config AgentConfig) (*AgentConfig, error)
	DeleteConfig(ctx context.Context, userID int64, id int64) error
}

type MySQLRepository struct {
	db *sql.DB
}

type scanner interface {
	Scan(dest ...any) error
}

type queryer interface {
	QueryContext(ctx context.Context, query string, args ...any) (*sql.Rows, error)
	QueryRowContext(ctx context.Context, query string, args ...any) *sql.Row
}

func NewRepository(db *sql.DB) *MySQLRepository {
	return &MySQLRepository{db: db}
}

func (r *MySQLRepository) ListAgents(ctx context.Context) ([]*AgentDefinition, error) {
	rows, err := r.db.QueryContext(
		ctx,
		selectAgentColumns+`
			ORDER BY id ASC
		`,
	)
	if err != nil {
		return nil, fmt.Errorf("query agents: %w", err)
	}
	defer rows.Close()

	agents := make([]*AgentDefinition, 0)
	for rows.Next() {
		agent, err := scanAgent(rows)
		if err != nil {
			return nil, fmt.Errorf("scan agent: %w", err)
		}
		agents = append(agents, agent)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate agents: %w", err)
	}

	return agents, nil
}

func (r *MySQLRepository) GetAgentByID(ctx context.Context, id int64) (*AgentDefinition, error) {
	row := r.db.QueryRowContext(
		ctx,
		selectAgentColumns+`
			WHERE id = ?
			LIMIT 1
		`,
		id,
	)

	agent, err := scanAgent(row)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, fmt.Errorf("query agent: %w", err)
	}
	return agent, nil
}

func (r *MySQLRepository) CreateConfig(ctx context.Context, config AgentConfig) (*AgentConfig, error) {
	tx, err := r.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("begin transaction: %w", err)
	}
	defer func() {
		_ = tx.Rollback()
	}()

	if config.IsDefault {
		if err := clearDefaultTx(ctx, tx, config.UserID, config.AgentID, 0); err != nil {
			return nil, err
		}
	}

	result, err := tx.ExecContext(
		ctx,
		`
			INSERT INTO agent_configs (
				user_id, agent_id, name, description, is_default, status
			)
			VALUES (?, ?, ?, ?, ?, ?)
		`,
		config.UserID,
		config.AgentID,
		config.Name,
		config.Description,
		config.IsDefault,
		config.Status,
	)
	if err != nil {
		return nil, mapWriteError("insert agent config", err)
	}

	id, err := result.LastInsertId()
	if err != nil {
		return nil, fmt.Errorf("read inserted id: %w", err)
	}
	if err := replaceBindingsTx(ctx, tx, id, config.NodeBindings); err != nil {
		return nil, err
	}

	created, err := getConfigByIDTx(ctx, tx, config.UserID, id)
	if err != nil {
		return nil, err
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("commit transaction: %w", err)
	}

	return created, nil
}

func (r *MySQLRepository) ListConfigsByUserID(ctx context.Context, userID int64, agentID int64) ([]*AgentConfig, error) {
	query := selectAgentConfigColumns + `
		WHERE c.user_id = ?
	`
	args := []any{userID}
	if agentID > 0 {
		query += ` AND c.agent_id = ?`
		args = append(args, agentID)
	}
	query += `
		ORDER BY c.is_default DESC, c.updated_at DESC, c.id DESC
	`

	rows, err := r.db.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, fmt.Errorf("query agent configs: %w", err)
	}
	defer rows.Close()

	configs := make([]*AgentConfig, 0)
	for rows.Next() {
		config, err := scanAgentConfig(rows)
		if err != nil {
			return nil, fmt.Errorf("scan agent config: %w", err)
		}
		if err := loadBindings(ctx, r.db, config); err != nil {
			return nil, err
		}
		configs = append(configs, config)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate agent configs: %w", err)
	}

	return configs, nil
}

func (r *MySQLRepository) GetConfigByID(ctx context.Context, userID int64, id int64) (*AgentConfig, error) {
	row := r.db.QueryRowContext(
		ctx,
		selectAgentConfigColumns+`
			WHERE c.user_id = ? AND c.id = ?
			LIMIT 1
		`,
		userID,
		id,
	)

	config, err := scanAgentConfig(row)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, fmt.Errorf("query agent config: %w", err)
	}
	if err := loadBindings(ctx, r.db, config); err != nil {
		return nil, err
	}

	return config, nil
}

func (r *MySQLRepository) UpdateConfig(ctx context.Context, config AgentConfig) (*AgentConfig, error) {
	tx, err := r.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("begin transaction: %w", err)
	}
	defer func() {
		_ = tx.Rollback()
	}()

	if config.IsDefault {
		if err := clearDefaultTx(ctx, tx, config.UserID, config.AgentID, config.ID); err != nil {
			return nil, err
		}
	}

	result, err := tx.ExecContext(
		ctx,
		`
			UPDATE agent_configs
			SET
				name = ?,
				description = ?,
				is_default = ?,
				status = ?
			WHERE user_id = ? AND id = ?
		`,
		config.Name,
		config.Description,
		config.IsDefault,
		config.Status,
		config.UserID,
		config.ID,
	)
	if err != nil {
		return nil, mapWriteError("update agent config", err)
	}

	rowsAffected, err := result.RowsAffected()
	if err != nil {
		return nil, fmt.Errorf("read updated rows: %w", err)
	}
	if rowsAffected == 0 {
		exists, err := configExistsTx(ctx, tx, config.UserID, config.ID)
		if err != nil {
			return nil, err
		}
		if !exists {
			return nil, ErrNotFound
		}
	}
	if err := replaceBindingsTx(ctx, tx, config.ID, config.NodeBindings); err != nil {
		return nil, err
	}

	updated, err := getConfigByIDTx(ctx, tx, config.UserID, config.ID)
	if err != nil {
		return nil, err
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("commit transaction: %w", err)
	}

	return updated, nil
}

func configExistsTx(ctx context.Context, tx *sql.Tx, userID int64, id int64) (bool, error) {
	var exists bool
	if err := tx.QueryRowContext(
		ctx,
		`SELECT EXISTS(
			SELECT 1
			FROM agent_configs
			WHERE user_id = ? AND id = ?
		)`,
		userID,
		id,
	).Scan(&exists); err != nil {
		return false, fmt.Errorf("check agent config existence: %w", err)
	}
	return exists, nil
}

func (r *MySQLRepository) DeleteConfig(ctx context.Context, userID int64, id int64) error {
	result, err := r.db.ExecContext(
		ctx,
		`DELETE FROM agent_configs WHERE user_id = ? AND id = ?`,
		userID,
		id,
	)
	if err != nil {
		return fmt.Errorf("delete agent config: %w", err)
	}

	rowsAffected, err := result.RowsAffected()
	if err != nil {
		return fmt.Errorf("read deleted rows: %w", err)
	}
	if rowsAffected == 0 {
		return ErrNotFound
	}

	return nil
}

func getConfigByIDTx(ctx context.Context, tx *sql.Tx, userID int64, id int64) (*AgentConfig, error) {
	row := tx.QueryRowContext(
		ctx,
		selectAgentConfigColumns+`
			WHERE c.user_id = ? AND c.id = ?
			LIMIT 1
		`,
		userID,
		id,
	)

	config, err := scanAgentConfig(row)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, fmt.Errorf("query agent config: %w", err)
	}
	if err := loadBindings(ctx, tx, config); err != nil {
		return nil, err
	}

	return config, nil
}

func clearDefaultTx(ctx context.Context, tx *sql.Tx, userID int64, agentID int64, excludeID int64) error {
	query := `UPDATE agent_configs SET is_default = 0 WHERE user_id = ? AND agent_id = ?`
	args := []any{userID, agentID}
	if excludeID > 0 {
		query += ` AND id <> ?`
		args = append(args, excludeID)
	}

	if _, err := tx.ExecContext(ctx, query, args...); err != nil {
		return fmt.Errorf("clear default agent config: %w", err)
	}

	return nil
}

func replaceBindingsTx(ctx context.Context, tx *sql.Tx, configID int64, bindings []NodeBinding) error {
	if _, err := tx.ExecContext(
		ctx,
		`DELETE FROM agent_config_node_bindings WHERE agent_config_id = ?`,
		configID,
	); err != nil {
		return fmt.Errorf("delete agent config node bindings: %w", err)
	}

	for _, binding := range bindings {
		if _, err := tx.ExecContext(
			ctx,
			`
				INSERT INTO agent_config_node_bindings (
					agent_config_id, node_key, model_config_id, temperature, max_tokens
				)
				VALUES (?, ?, ?, ?, ?)
			`,
			configID,
			binding.NodeKey,
			binding.ModelConfigID,
			nullableFloat64(binding.Temperature),
			nullableInt(binding.MaxTokens),
		); err != nil {
			return mapWriteError("insert agent config node binding", err)
		}
	}

	return nil
}

func loadBindings(ctx context.Context, q queryer, config *AgentConfig) error {
	rows, err := q.QueryContext(
		ctx,
		`
			SELECT id, agent_config_id, node_key, model_config_id, temperature, max_tokens, created_at, updated_at
			FROM agent_config_node_bindings
			WHERE agent_config_id = ?
			ORDER BY id ASC
		`,
		config.ID,
	)
	if err != nil {
		return fmt.Errorf("query agent config node bindings: %w", err)
	}
	defer rows.Close()

	bindings := make([]NodeBinding, 0)
	for rows.Next() {
		binding, err := scanNodeBinding(rows)
		if err != nil {
			return fmt.Errorf("scan agent config node binding: %w", err)
		}
		bindings = append(bindings, binding)
	}
	if err := rows.Err(); err != nil {
		return fmt.Errorf("iterate agent config node bindings: %w", err)
	}

	config.NodeBindings = bindings
	return nil
}

func scanAgent(src scanner) (*AgentDefinition, error) {
	var agent AgentDefinition
	var rawNodes []byte
	if err := src.Scan(
		&agent.ID,
		&agent.AgentKey,
		&agent.Name,
		&agent.Description,
		&rawNodes,
		&agent.Status,
		&agent.CreatedAt,
		&agent.UpdatedAt,
	); err != nil {
		return nil, err
	}

	if err := json.Unmarshal(rawNodes, &agent.Nodes); err != nil {
		return nil, fmt.Errorf("decode agent node_schema: %w", err)
	}
	if agent.Nodes == nil {
		agent.Nodes = []NodeDefinition{}
	}

	return &agent, nil
}

func scanAgentConfig(src scanner) (*AgentConfig, error) {
	var config AgentConfig
	if err := src.Scan(
		&config.ID,
		&config.UserID,
		&config.AgentID,
		&config.AgentKey,
		&config.AgentName,
		&config.Name,
		&config.Description,
		&config.IsDefault,
		&config.Status,
		&config.CreatedAt,
		&config.UpdatedAt,
	); err != nil {
		return nil, err
	}

	return &config, nil
}

func scanNodeBinding(src scanner) (NodeBinding, error) {
	var binding NodeBinding
	var temperature sql.NullFloat64
	var maxTokens sql.NullInt64
	if err := src.Scan(
		&binding.ID,
		&binding.AgentConfigID,
		&binding.NodeKey,
		&binding.ModelConfigID,
		&temperature,
		&maxTokens,
		&binding.CreatedAt,
		&binding.UpdatedAt,
	); err != nil {
		return NodeBinding{}, err
	}
	if temperature.Valid {
		value := temperature.Float64
		binding.Temperature = &value
	}
	if maxTokens.Valid {
		value := int(maxTokens.Int64)
		binding.MaxTokens = &value
	}

	return binding, nil
}

func nullableFloat64(value *float64) any {
	if value == nil {
		return nil
	}
	return *value
}

func nullableInt(value *int) any {
	if value == nil {
		return nil
	}
	return *value
}

func mapWriteError(action string, err error) error {
	var mysqlErr *mysqlDriver.MySQLError
	if errors.As(err, &mysqlErr) && mysqlErr.Number == 1062 {
		return ErrNameAlreadyExists
	}

	return fmt.Errorf("%s: %w", action, err)
}
