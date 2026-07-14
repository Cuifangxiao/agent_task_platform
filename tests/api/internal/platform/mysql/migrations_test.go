package mysql

import (
	"os"
	"path/filepath"
	"testing"
)

func TestFindDirectoryUpward(t *testing.T) {
	root := t.TempDir()
	migrationDir := filepath.Join(root, "migrations", "mysql")
	start := filepath.Join(root, "apps", "api", "config")
	if err := os.MkdirAll(migrationDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(start, 0o755); err != nil {
		t.Fatal(err)
	}

	got, ok := findDirectoryUpward(start, filepath.Join("migrations", "mysql"))
	if !ok {
		t.Fatal("migration directory was not found")
	}
	if got != migrationDir {
		t.Fatalf("directory = %q, want %q", got, migrationDir)
	}
}

func TestSplitSQLStatementsKeepsQuotedSemicolons(t *testing.T) {
	script := "INSERT INTO sample(value) VALUES ('a;b'); UPDATE sample SET value = \"c;d\";"
	statements := splitSQLStatements(script)
	if len(statements) != 2 {
		t.Fatalf("statements = %#v", statements)
	}
}
