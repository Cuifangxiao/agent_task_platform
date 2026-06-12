package modelconfig

import (
	"context"
	"database/sql"
	"errors"
	"fmt"

	mysqlDriver "github.com/go-sql-driver/mysql"
)

var ErrNameAlreadyExists = errors.New("model config name already exists")

type Repository interface {
	Create(ctx context.Context, config ModelConfig) (*ModelConfig, error)
}

type MySQLRepository struct {
	db *sql.DB
}

func NewRepository(db *sql.DB) *MySQLRepository {
	return &MySQLRepository{db: db}
}

func (r *MySQLRepository) Create(ctx context.Context, config ModelConfig) (*ModelConfig, error) {
	tx, err := r.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("begin transaction: %w", err)
	}

	defer func() {
		_ = tx.Rollback()
	}()

	if config.IsDefault {
		if _, err := tx.ExecContext(
			ctx,
			`UPDATE model_configs SET is_default = 0 WHERE user_id = ?`,
			config.UserID,
		); err != nil {
			return nil, fmt.Errorf("clear default model config: %w", err)
		}
	}

	result, err := tx.ExecContext(
		ctx,
		`
			INSERT INTO model_configs (
				user_id, name, provider, model_name, base_url, api_key,
				temperature, max_tokens, is_default, status
			)
			VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		`,
		config.UserID,
		config.Name,
		config.Provider,
		config.ModelName,
		config.BaseURL,
		config.APIKey,
		config.Temperature,
		config.MaxTokens,
		config.IsDefault,
		config.Status,
	)
	if err != nil {
		var mysqlErr *mysqlDriver.MySQLError
		if errors.As(err, &mysqlErr) && mysqlErr.Number == 1062 {
			return nil, ErrNameAlreadyExists
		}

		return nil, fmt.Errorf("insert model config: %w", err)
	}

	id, err := result.LastInsertId()
	if err != nil {
		return nil, fmt.Errorf("read inserted id: %w", err)
	}

	created, err := getByIDTx(ctx, tx, id)
	if err != nil {
		return nil, err
	}

	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("commit transaction: %w", err)
	}

	return created, nil
}

func getByIDTx(ctx context.Context, tx *sql.Tx, id int64) (*ModelConfig, error) {
	row := tx.QueryRowContext(
		ctx,
		`
			SELECT
				id, user_id, name, provider, model_name, base_url, api_key,
				temperature, max_tokens, is_default, status, created_at, updated_at
			FROM model_configs
			WHERE id = ?
			LIMIT 1
		`,
		id,
	)

	var config ModelConfig
	if err := row.Scan(
		&config.ID,
		&config.UserID,
		&config.Name,
		&config.Provider,
		&config.ModelName,
		&config.BaseURL,
		&config.APIKey,
		&config.Temperature,
		&config.MaxTokens,
		&config.IsDefault,
		&config.Status,
		&config.CreatedAt,
		&config.UpdatedAt,
	); err != nil {
		return nil, fmt.Errorf("query created model config: %w", err)
	}

	return &config, nil
}
