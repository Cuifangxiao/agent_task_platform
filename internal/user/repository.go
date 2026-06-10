package user

import (
	"context"
	"database/sql"
	"errors"
)

type Repository interface {
	GetByUsername(ctx context.Context, username string) (*User, error)
}

type MySQLRepository struct {
	db *sql.DB
}

func NewRepository(db *sql.DB) *MySQLRepository {
	return &MySQLRepository{db: db}
}

func (r *MySQLRepository) GetByUsername(ctx context.Context, username string) (*User, error) {
	query := `
		SELECT id, username, password_hash, created_at, updated_at
		FROM users
		WHERE username = ?
		LIMIT 1
	`

	var user User
	err := r.db.QueryRowContext(ctx, query, username).Scan(
		&user.ID,
		&user.Username,
		&user.PasswordHash,
		&user.CreatedAt,
		&user.UpdatedAt,
	)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, nil
		}
		return nil, err
	}

	return &user, nil
}
