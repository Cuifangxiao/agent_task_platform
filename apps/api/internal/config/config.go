// Package config loads and validates the API service configuration.
package config

import (
	"fmt"
	"net/url"
	"os"
	"time"

	"gopkg.in/yaml.v3"
)

type Config struct {
	App         AppConfig         `yaml:"app"`
	Server      ServerConfig      `yaml:"server"`
	MySQL       MySQLConfig       `yaml:"mysql"`
	Redis       RedisConfig       `yaml:"redis"`
	JWT         JWTConfig         `yaml:"jwt"`
	PythonAgent PythonAgentConfig `yaml:"python_agent"`
}

type AppConfig struct {
	Name string `yaml:"name"`
	Env  string `yaml:"env"`
}

type ServerConfig struct {
	Host string `yaml:"host"`
	Port int    `yaml:"port"`
}

type MySQLConfig struct {
	Host      string `yaml:"host"`
	Port      int    `yaml:"port"`
	Username  string `yaml:"username"`
	Password  string `yaml:"password"`
	Database  string `yaml:"database"`
	Charset   string `yaml:"charset"`
	ParseTime bool   `yaml:"parse_time"`
	Loc       string `yaml:"loc"`
}

type RedisConfig struct {
	Host     string `yaml:"host"`
	Port     int    `yaml:"port"`
	Password string `yaml:"password"`
	DB       int    `yaml:"db"`
}

type JWTConfig struct {
	Secret              string `yaml:"secret"`
	Issuer              string `yaml:"issuer"`
	ExpireHours         int    `yaml:"expire_hours"`
	AccessExpireMinutes int    `yaml:"access_expire_minutes"`
	RefreshExpireHours  int    `yaml:"refresh_expire_hours"`
}

type PythonAgentConfig struct {
	Host         string `yaml:"host"`
	Port         int    `yaml:"port"`
	ArtifactsDir string `yaml:"artifacts_dir"`
}

func Load(path string) (*Config, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}

	var cfg Config
	if err := yaml.Unmarshal(data, &cfg); err != nil {
		return nil, err
	}
	cfg.setDefaults()
	if err := cfg.Validate(); err != nil {
		return nil, err
	}

	return &cfg, nil
}

func (c *Config) setDefaults() {
	if c.App.Name == "" {
		c.App.Name = "agent-task-platform"
	}
	if c.App.Env == "" {
		c.App.Env = "local"
	}
	if c.Server.Host == "" {
		c.Server.Host = "0.0.0.0"
	}
	if c.Server.Port == 0 {
		c.Server.Port = 8080
	}
	if c.MySQL.Host == "" {
		c.MySQL.Host = "127.0.0.1"
	}
	if c.MySQL.Port == 0 {
		c.MySQL.Port = 3306
	}
	if c.MySQL.Charset == "" {
		c.MySQL.Charset = "utf8mb4"
	}
	// Durable PPT run timestamps are scanned into time.Time. go-sql-driver
	// requires parseTime=true for DATETIME columns, so this is a mandatory
	// runtime invariant rather than an optional tuning flag.
	c.MySQL.ParseTime = true
	// DATETIME has no timezone. Pin the driver location so deployments in
	// different host timezones never reinterpret durable run timestamps.
	c.MySQL.Loc = "UTC"
	if c.Redis.Host == "" {
		c.Redis.Host = "127.0.0.1"
	}
	if c.Redis.Port == 0 {
		c.Redis.Port = 6379
	}
	if c.JWT.Issuer == "" {
		c.JWT.Issuer = c.App.Name
	}
	if c.JWT.AccessExpireMinutes == 0 && c.JWT.ExpireHours == 0 {
		c.JWT.AccessExpireMinutes = 30
	}
	if c.JWT.RefreshExpireHours == 0 {
		c.JWT.RefreshExpireHours = 168
	}
	if c.PythonAgent.Host == "" {
		c.PythonAgent.Host = "127.0.0.1"
	}
	if c.PythonAgent.Port == 0 {
		c.PythonAgent.Port = 50051
	}
	if c.PythonAgent.ArtifactsDir == "" {
		c.PythonAgent.ArtifactsDir = "./apps/runtime/artifacts/decks"
	}
}

func (c Config) Validate() error {
	if c.MySQL.Username == "" {
		return fmt.Errorf("mysql.username is required")
	}
	if c.MySQL.Database == "" {
		return fmt.Errorf("mysql.database is required")
	}
	if c.JWT.Secret == "" {
		return fmt.Errorf("jwt.secret is required")
	}
	if err := validatePort("server.port", c.Server.Port); err != nil {
		return err
	}
	if err := validatePort("mysql.port", c.MySQL.Port); err != nil {
		return err
	}
	if err := validatePort("redis.port", c.Redis.Port); err != nil {
		return err
	}
	if err := validatePort("python_agent.port", c.PythonAgent.Port); err != nil {
		return err
	}

	return nil
}

func validatePort(name string, port int) error {
	if port <= 0 || port > 65535 {
		return fmt.Errorf("%s must be between 1 and 65535", name)
	}

	return nil
}

func (c ServerConfig) Address() string {
	return fmt.Sprintf("%s:%d", c.Host, c.Port)
}

func (c MySQLConfig) Address() string {
	return fmt.Sprintf("%s:%d", c.Host, c.Port)
}

func (c MySQLConfig) DSN() string {
	return fmt.Sprintf(
		"%s:%s@tcp(%s:%d)/%s?charset=%s&parseTime=%t&loc=%s",
		c.Username,
		c.Password,
		c.Host,
		c.Port,
		c.Database,
		c.Charset,
		c.ParseTime,
		url.QueryEscape(c.Loc),
	)
}

func (c RedisConfig) Address() string {
	return fmt.Sprintf("%s:%d", c.Host, c.Port)
}

func (c PythonAgentConfig) GRPCAddress() string {
	return fmt.Sprintf("%s:%d", c.Host, c.Port)
}

func (c JWTConfig) AccessExpireDuration() time.Duration {
	if c.AccessExpireMinutes > 0 {
		return time.Duration(c.AccessExpireMinutes) * time.Minute
	}

	if c.ExpireHours > 0 {
		return time.Duration(c.ExpireHours) * time.Hour
	}

	return 30 * time.Minute
}

func (c JWTConfig) RefreshExpireDuration() time.Duration {
	if c.RefreshExpireHours > 0 {
		return time.Duration(c.RefreshExpireHours) * time.Hour
	}

	return 7 * 24 * time.Hour
}

func (c JWTConfig) ExpireDuration() time.Duration {
	return c.AccessExpireDuration()
}
