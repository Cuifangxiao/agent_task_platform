package auth

import (
	"crypto/rand"
	"encoding/hex"
	"errors"
	"time"

	"github.com/golang-jwt/jwt/v5"
)

var ErrInvalidToken = errors.New("invalid token")
var ErrUnexpectedTokenType = errors.New("unexpected token type")

type TokenType string

const (
	TokenTypeAccess  TokenType = "access"
	TokenTypeRefresh TokenType = "refresh"
)

type Claims struct {
	UserID    int64     `json:"user_id"`
	Username  string    `json:"username"`
	TokenType TokenType `json:"token_type"`
	SessionID string    `json:"session_id"`
	Device    string    `json:"device"`
	jwt.RegisteredClaims
}

type IssuedToken struct {
	Token     string
	JTI       string
	ExpiresAt time.Time
}

type Manager struct {
	secret           []byte
	issuer           string
	accessExpireTTL  time.Duration
	refreshExpireTTL time.Duration
}

func NewManager(secret string, issuer string, accessExpireTTL time.Duration, refreshExpireTTL time.Duration) *Manager {
	return &Manager{
		secret:           []byte(secret),
		issuer:           issuer,
		accessExpireTTL:  accessExpireTTL,
		refreshExpireTTL: refreshExpireTTL,
	}
}

func (m *Manager) GenerateAccessToken(userID int64, username string, sessionID string, device string) (*IssuedToken, error) {
	return m.generateToken(userID, username, sessionID, device, TokenTypeAccess, m.accessExpireTTL)
}

func (m *Manager) GenerateRefreshToken(userID int64, username string, sessionID string, device string) (*IssuedToken, error) {
	return m.generateToken(userID, username, sessionID, device, TokenTypeRefresh, m.refreshExpireTTL)
}

func (m *Manager) generateToken(userID int64, username string, sessionID string, device string, tokenType TokenType, ttl time.Duration) (*IssuedToken, error) {
	now := time.Now()
	jti, err := newTokenID()
	if err != nil {
		return nil, err
	}

	claims := Claims{
		UserID:    userID,
		Username:  username,
		TokenType: tokenType,
		SessionID: sessionID,
		Device:    device,
		RegisteredClaims: jwt.RegisteredClaims{
			Issuer:    m.issuer,
			Subject:   username,
			ID:        jti,
			IssuedAt:  jwt.NewNumericDate(now),
			ExpiresAt: jwt.NewNumericDate(now.Add(ttl)),
		},
	}

	token := jwt.NewWithClaims(jwt.SigningMethodHS256, claims)
	tokenString, err := token.SignedString(m.secret)
	if err != nil {
		return nil, err
	}

	return &IssuedToken{
		Token:     tokenString,
		JTI:       jti,
		ExpiresAt: claims.ExpiresAt.Time,
	}, nil
}

func (m *Manager) ParseToken(tokenString string) (*Claims, error) {
	token, err := jwt.ParseWithClaims(
		tokenString,
		&Claims{},
		func(token *jwt.Token) (any, error) {
			return m.secret, nil
		},
		jwt.WithIssuer(m.issuer),
		jwt.WithValidMethods([]string{jwt.SigningMethodHS256.Alg()}),
	)
	if err != nil {
		return nil, err
	}

	claims, ok := token.Claims.(*Claims)
	if !ok || !token.Valid {
		return nil, ErrInvalidToken
	}

	return claims, nil
}

func (m *Manager) AccessTokenTTL() time.Duration {
	return m.accessExpireTTL
}

func (m *Manager) RefreshTokenTTL() time.Duration {
	return m.refreshExpireTTL
}

func (c *Claims) RequireTokenType(expected TokenType) error {
	if c.TokenType != expected {
		return ErrUnexpectedTokenType
	}

	return nil
}

func newTokenID() (string, error) {
	buf := make([]byte, 16)
	if _, err := rand.Read(buf); err != nil {
		return "", err
	}

	return hex.EncodeToString(buf), nil
}
