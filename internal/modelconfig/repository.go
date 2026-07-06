package modelconfig

import (
	"context"
	"database/sql"
	"errors"
	"fmt"

	mysqlDriver "github.com/go-sql-driver/mysql"
)

var ErrNameAlreadyExists = errors.New("model config name already exists")

const selectModelConfigColumns = `
	SELECT
		id, user_id, name, provider, model_name, base_url, api_key, provider_options,
		temperature, max_tokens, is_default, status, created_at, updated_at
	FROM model_configs
`

type Repository interface {
	Create(ctx context.Context, config ModelConfig) (*ModelConfig, error)
	ListByUserID(ctx context.Context, userID int64) ([]*ModelConfig, error)
	GetByID(ctx context.Context, userID int64, id int64) (*ModelConfig, error)
	Update(ctx context.Context, config ModelConfig) (*ModelConfig, error)
	Delete(ctx context.Context, userID int64, id int64) error
}

type MySQLRepository struct {
	db *sql.DB
}

type scanner interface {
	Scan(dest ...any) error
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
		if err := clearDefaultTx(ctx, tx, config.UserID, 0); err != nil {
			return nil, err
		}
	}

	result, err := tx.ExecContext(
		ctx,
		`
			INSERT INTO model_configs (
				user_id, name, provider, model_name, base_url, api_key, provider_options,
				temperature, max_tokens, is_default, status
			)
			VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		`,
		config.UserID,
		config.Name,
		config.Provider,
		config.ModelName,
		config.BaseURL,
		config.APIKey,
		config.ProviderOptions,
		config.Temperature,
		config.MaxTokens,
		config.IsDefault,
		config.Status,
	)
	if err != nil {
		return nil, mapWriteError("insert model config", err)
	}

	id, err := result.LastInsertId()
	if err != nil {
		return nil, fmt.Errorf("read inserted id: %w", err)
	}

	created, err := getByIDTx(ctx, tx, config.UserID, id)
	if err != nil {
		return nil, err
	}

	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("commit transaction: %w", err)
	}

	return created, nil
}

func (r *MySQLRepository) ListByUserID(ctx context.Context, userID int64) ([]*ModelConfig, error) {
	rows, err := r.db.QueryContext(
		ctx,
		selectModelConfigColumns+`
			WHERE user_id = ?
			ORDER BY is_default DESC, updated_at DESC, id DESC
		`,
		userID,
	)
	if err != nil {
		return nil, fmt.Errorf("query model configs: %w", err)
	}
	defer rows.Close()

	configs := make([]*ModelConfig, 0)
	for rows.Next() {
		config, err := scanModelConfig(rows)
		if err != nil {
			return nil, fmt.Errorf("scan model config: %w", err)
		}
		configs = append(configs, config)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate model configs: %w", err)
	}

	return configs, nil
}

func (r *MySQLRepository) GetByID(ctx context.Context, userID int64, id int64) (*ModelConfig, error) {
	row := r.db.QueryRowContext(
		ctx,
		selectModelConfigColumns+`
			WHERE user_id = ? AND id = ?
			LIMIT 1
		`,
		userID,
		id,
	)

	config, err := scanModelConfig(row)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, fmt.Errorf("query model config: %w", err)
	}

	return config, nil
}

func (r *MySQLRepository) Update(ctx context.Context, config ModelConfig) (*ModelConfig, error) {
	tx, err := r.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("begin transaction: %w", err)
	}

	defer func() {
		_ = tx.Rollback()
	}()

	if config.IsDefault {
		if err := clearDefaultTx(ctx, tx, config.UserID, config.ID); err != nil {
			return nil, err
		}
	}

	result, err := tx.ExecContext(
		ctx,
		`
			UPDATE model_configs
			SET
				name = ?,
				provider = ?,
				model_name = ?,
				base_url = ?,
				api_key = ?,
				provider_options = ?,
				temperature = ?,
				max_tokens = ?,
				is_default = ?,
				status = ?
			WHERE user_id = ? AND id = ?
		`,
		config.Name,
		config.Provider,
		config.ModelName,
		config.BaseURL,
		config.APIKey,
		config.ProviderOptions,
		config.Temperature,
		config.MaxTokens,
		config.IsDefault,
		config.Status,
		config.UserID,
		config.ID,
	)
	if err != nil {
		return nil, mapWriteError("update model config", err)
	}

	rowsAffected, err := result.RowsAffected()
	if err != nil {
		return nil, fmt.Errorf("read updated rows: %w", err)
	}
	if rowsAffected == 0 {
		return nil, ErrNotFound
	}

	updated, err := getByIDTx(ctx, tx, config.UserID, config.ID)
	if err != nil {
		return nil, err
	}

	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("commit transaction: %w", err)
	}

	return updated, nil
}

func (r *MySQLRepository) Delete(ctx context.Context, userID int64, id int64) error {
	result, err := r.db.ExecContext(
		ctx,
		`DELETE FROM model_configs WHERE user_id = ? AND id = ?`,
		userID,
		id,
	)
	if err != nil {
		return fmt.Errorf("delete model config: %w", err)
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

func getByIDTx(ctx context.Context, tx *sql.Tx, userID int64, id int64) (*ModelConfig, error) {
	row := tx.QueryRowContext(
		ctx,
		selectModelConfigColumns+`
			WHERE user_id = ? AND id = ?
			LIMIT 1
		`,
		userID,
		id,
	)

	config, err := scanModelConfig(row)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, fmt.Errorf("query model config: %w", err)
	}

	return config, nil
}

func clearDefaultTx(ctx context.Context, tx *sql.Tx, userID int64, excludeID int64) error {
	query := `UPDATE model_configs SET is_default = 0 WHERE user_id = ?`
	args := []any{userID}
	if excludeID > 0 {
		query += ` AND id <> ?`
		args = append(args, excludeID)
	}

	if _, err := tx.ExecContext(ctx, query, args...); err != nil {
		return fmt.Errorf("clear default model config: %w", err)
	}

	return nil
}

func scanModelConfig(src scanner) (*ModelConfig, error) {
	var config ModelConfig
	var providerOptions []byte
	if err := src.Scan(
		&config.ID,
		&config.UserID,
		&config.Name,
		&config.Provider,
		&config.ModelName,
		&config.BaseURL,
		&config.APIKey,
		&providerOptions,
		&config.Temperature,
		&config.MaxTokens,
		&config.IsDefault,
		&config.Status,
		&config.CreatedAt,
		&config.UpdatedAt,
	); err != nil {
		return nil, err
	}
	config.ProviderOptions = string(providerOptions)

	return &config, nil
}

func mapWriteError(action string, err error) error {
	var mysqlErr *mysqlDriver.MySQLError
	if errors.As(err, &mysqlErr) && mysqlErr.Number == 1062 {
		return ErrNameAlreadyExists
	}

	return fmt.Errorf("%s: %w", action, err)
}
