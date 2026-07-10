package auth

import (
	"context"
	"time"
)

const ContextKeyClaims = "authClaims"

type SessionStatus string

const SessionStatusActive SessionStatus = "active"

type Session struct {
	UserID    int64         `json:"user_id"`
	Username  string        `json:"username"`
	SessionID string        `json:"session_id"`
	Device    string        `json:"device"`
	Status    SessionStatus `json:"status"`
	CreatedAt time.Time     `json:"created_at"`
	ExpiresAt time.Time     `json:"expires_at"`
}

type SessionStore interface {
	SaveSession(ctx context.Context, session Session, refreshToken string, ttl time.Duration) error
	GetSession(ctx context.Context, userID int64, device string) (*Session, error)
	GetRefreshToken(ctx context.Context, userID int64, device string) (string, error)
	DeleteSession(ctx context.Context, userID int64, device string) error
	BlacklistAccessToken(ctx context.Context, jti string, ttl time.Duration) error
	IsAccessTokenBlacklisted(ctx context.Context, jti string) (bool, error)
}
