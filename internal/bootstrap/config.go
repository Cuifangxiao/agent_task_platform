package bootstrap

import (
	"fmt"
	"os"
	"time"

	"gopkg.in/yaml.v3"
)

type Config struct {
	App    AppConfig    `yaml:"app"`
	Server ServerConfig `yaml:"server"`
	MySQL  MySQLConfig  `yaml:"mysql"`
	Redis  RedisConfig  `yaml:"redis"`
	JWT    JWTConfig    `yaml:"jwt"`
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

func LoadConfig(path string) (*Config, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}

	var cfg Config
	if err := yaml.Unmarshal(data, &cfg); err != nil {
		return nil, err
	}

	return &cfg, nil
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
		c.Loc,
	)
}

func (c RedisConfig) Address() string {
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
