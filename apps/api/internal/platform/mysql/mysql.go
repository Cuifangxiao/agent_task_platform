// Package mysql owns MySQL connectivity and schema migrations.
package mysql

import (
	"context"
	"database/sql"
	"fmt"
	"time"

	"agent-task-platform/apps/api/internal/config"

	_ "github.com/go-sql-driver/mysql"
)

func Open(cfg config.MySQLConfig) (*sql.DB, error) {
	db, err := sql.Open("mysql", cfg.DSN())
	if err != nil {
		return nil, fmt.Errorf("open mysql failed: %w", err)
	}

	db.SetMaxOpenConns(20)
	db.SetMaxIdleConns(10)
	db.SetConnMaxLifetime(30 * time.Minute)

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	if err := db.PingContext(ctx); err != nil {
		_ = db.Close()
		return nil, fmt.Errorf("ping mysql failed: %w", err)
	}

	return db, nil
}
