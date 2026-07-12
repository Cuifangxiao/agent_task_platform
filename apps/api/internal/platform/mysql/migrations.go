package mysql

import (
	"context"
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

const createSchemaMigrationsTableSQL = `
CREATE TABLE IF NOT EXISTS schema_migrations (
    version VARCHAR(255) NOT NULL PRIMARY KEY,
    applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
`

func Migrate(ctx context.Context, db *sql.DB, confPath string) ([]string, error) {
	if db == nil {
		return nil, fmt.Errorf("mysql db is required")
	}

	dir, err := resolveMySQLMigrationDir(confPath)
	if err != nil {
		return nil, err
	}

	conn, err := db.Conn(ctx)
	if err != nil {
		return nil, fmt.Errorf("acquire mysql migration connection failed: %w", err)
	}
	defer conn.Close()

	if _, err := conn.ExecContext(ctx, createSchemaMigrationsTableSQL); err != nil {
		return nil, fmt.Errorf("ensure schema_migrations table failed: %w", err)
	}

	applied, err := loadAppliedMigrations(ctx, conn)
	if err != nil {
		return nil, err
	}

	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, fmt.Errorf("read mysql migration directory failed: %w", err)
	}

	appliedNames := make([]string, 0)
	for _, entry := range entries {
		if entry.IsDir() || filepath.Ext(entry.Name()) != ".sql" {
			continue
		}
		if _, ok := applied[entry.Name()]; ok {
			continue
		}

		path := filepath.Join(dir, entry.Name())
		script, err := os.ReadFile(path)
		if err != nil {
			return nil, fmt.Errorf("read migration %s failed: %w", entry.Name(), err)
		}

		statements := splitSQLStatements(string(script))
		for _, statement := range statements {
			if _, err := conn.ExecContext(ctx, statement); err != nil {
				return nil, fmt.Errorf("apply migration %s failed: %w", entry.Name(), err)
			}
		}

		if _, err := conn.ExecContext(
			ctx,
			`INSERT INTO schema_migrations (version) VALUES (?)`,
			entry.Name(),
		); err != nil {
			return nil, fmt.Errorf("record migration %s failed: %w", entry.Name(), err)
		}

		appliedNames = append(appliedNames, entry.Name())
	}

	return appliedNames, nil
}

func resolveMySQLMigrationDir(confPath string) (string, error) {
	starts := make([]string, 0, 2)
	if strings.TrimSpace(confPath) != "" {
		configAbs, err := filepath.Abs(confPath)
		if err == nil {
			starts = append(starts, filepath.Dir(configAbs))
		}
	}
	if cwd, err := os.Getwd(); err == nil {
		starts = append(starts, cwd)
	}

	for _, start := range starts {
		if dir, ok := findDirectoryUpward(start, filepath.Join("migrations", "mysql")); ok {
			return dir, nil
		}
	}

	return "", fmt.Errorf("mysql migration directory not found")
}

func findDirectoryUpward(start string, relativePath string) (string, bool) {
	current := filepath.Clean(start)
	for {
		candidate := filepath.Join(current, relativePath)
		if info, err := os.Stat(candidate); err == nil && info.IsDir() {
			return filepath.Clean(candidate), true
		}

		parent := filepath.Dir(current)
		if parent == current {
			return "", false
		}
		current = parent
	}
}

type migrationQueryer interface {
	QueryContext(ctx context.Context, query string, args ...any) (*sql.Rows, error)
}

func loadAppliedMigrations(ctx context.Context, db migrationQueryer) (map[string]struct{}, error) {
	rows, err := db.QueryContext(ctx, `SELECT version FROM schema_migrations`)
	if err != nil {
		return nil, fmt.Errorf("query applied migrations failed: %w", err)
	}
	defer rows.Close()

	applied := make(map[string]struct{})
	for rows.Next() {
		var version string
		if err := rows.Scan(&version); err != nil {
			return nil, fmt.Errorf("scan applied migration failed: %w", err)
		}
		applied[version] = struct{}{}
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate applied migrations failed: %w", err)
	}

	return applied, nil
}

func splitSQLStatements(script string) []string {
	script = strings.TrimPrefix(script, "\uFEFF")

	statements := make([]string, 0)
	var current strings.Builder
	inSingleQuote := false
	inDoubleQuote := false
	inBacktick := false
	escaped := false

	for _, r := range script {
		switch r {
		case '\\':
			if inSingleQuote || inDoubleQuote {
				escaped = !escaped
			}
		case '\'':
			if !escaped && !inDoubleQuote && !inBacktick {
				inSingleQuote = !inSingleQuote
			}
			escaped = false
		case '"':
			if !escaped && !inSingleQuote && !inBacktick {
				inDoubleQuote = !inDoubleQuote
			}
			escaped = false
		case '`':
			if !inSingleQuote && !inDoubleQuote {
				inBacktick = !inBacktick
			}
			escaped = false
		case ';':
			if !inSingleQuote && !inDoubleQuote && !inBacktick {
				statement := strings.TrimSpace(current.String())
				if statement != "" {
					statements = append(statements, statement)
				}
				current.Reset()
				escaped = false
				continue
			}
			escaped = false
		default:
			escaped = false
		}

		current.WriteRune(r)
	}

	if tail := strings.TrimSpace(current.String()); tail != "" {
		statements = append(statements, tail)
	}

	return statements
}
