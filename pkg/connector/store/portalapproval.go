// mautrix-telegram - A Matrix-Telegram puppeting bridge.
// Copyright (C) 2026 Tulir Asokan
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.

package store

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"go.mau.fi/util/dbutil"
	"maunium.net/go/mautrix/bridgev2/networkid"
)

type PortalApprovalStatus string

const (
	PortalApprovalPending PortalApprovalStatus = "pending"
	PortalApprovalAllowed PortalApprovalStatus = "allowed"
	PortalApprovalDenied  PortalApprovalStatus = "denied"
)

type PortalApproval struct {
	ApprovalID     int64
	UserID         int64
	PortalID       networkid.PortalID
	PortalReceiver networkid.UserLoginID
	PeerType       string
	EntityID       int64
	TopicID        int
	Title          string
	Username       string
	Status         PortalApprovalStatus
	LastEvent      string
	CreatedTS      int64
	LastSeenTS     int64
}

type PortalApprovalQuery struct {
	db *dbutil.Database
}

const (
	getNextPortalApprovalIDQuery = "SELECT COALESCE(MAX(approval_id), 0) + 1 FROM telegram_portal_approval"
	upsertPortalApprovalQuery    = `
		INSERT INTO telegram_portal_approval (
			approval_id, user_id, portal_id, portal_receiver, peer_type, entity_id, topic_id,
			title, username, status, last_event, created_ts, last_seen_ts
		) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
		ON CONFLICT (user_id, portal_id, portal_receiver) DO UPDATE SET
			peer_type=excluded.peer_type,
			entity_id=excluded.entity_id,
			topic_id=excluded.topic_id,
			title=excluded.title,
			username=excluded.username,
			last_event=excluded.last_event,
			last_seen_ts=excluded.last_seen_ts
	`
	upsertPortalApprovalWithStatusQuery = `
		INSERT INTO telegram_portal_approval (
			approval_id, user_id, portal_id, portal_receiver, peer_type, entity_id, topic_id,
			title, username, status, last_event, created_ts, last_seen_ts
		) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
		ON CONFLICT (user_id, portal_id, portal_receiver) DO UPDATE SET
			peer_type=excluded.peer_type,
			entity_id=excluded.entity_id,
			topic_id=excluded.topic_id,
			title=excluded.title,
			username=excluded.username,
			status=excluded.status,
			last_event=excluded.last_event,
			last_seen_ts=excluded.last_seen_ts
	`
	getPortalApprovalByPortalQuery = `
		SELECT approval_id, user_id, portal_id, portal_receiver, peer_type, entity_id, topic_id,
		       title, username, status, last_event, created_ts, last_seen_ts
		FROM telegram_portal_approval
		WHERE user_id=$1 AND portal_id=$2 AND portal_receiver=$3
	`
	getPortalApprovalByIDQuery = `
		SELECT approval_id, user_id, portal_id, portal_receiver, peer_type, entity_id, topic_id,
		       title, username, status, last_event, created_ts, last_seen_ts
		FROM telegram_portal_approval
		WHERE user_id=$1 AND approval_id=$2
	`
	getPortalApprovalByStatusQuery = `
		SELECT approval_id, user_id, portal_id, portal_receiver, peer_type, entity_id, topic_id,
		       title, username, status, last_event, created_ts, last_seen_ts
		FROM telegram_portal_approval
		WHERE user_id=$1 AND status=$2
		ORDER BY approval_id
	`
	setPortalApprovalStatusQuery        = "UPDATE telegram_portal_approval SET status=$3, last_seen_ts=$4 WHERE user_id=$1 AND approval_id=$2"
	deleteOldPendingPortalApprovalQuery = "DELETE FROM telegram_portal_approval WHERE user_id=$1 AND status=$2 AND last_seen_ts<$3"
)

var portalApprovalScanner = dbutil.ConvertRowFn[PortalApproval](func(row dbutil.Scannable) (item PortalApproval, err error) {
	var portalID, portalReceiver, status string
	err = row.Scan(
		&item.ApprovalID, &item.UserID, &portalID, &portalReceiver, &item.PeerType,
		&item.EntityID, &item.TopicID, &item.Title, &item.Username, &status,
		&item.LastEvent, &item.CreatedTS, &item.LastSeenTS,
	)
	item.PortalID = networkid.PortalID(portalID)
	item.PortalReceiver = networkid.UserLoginID(portalReceiver)
	item.Status = PortalApprovalStatus(status)
	return
})

func (q *PortalApprovalQuery) Upsert(ctx context.Context, item PortalApproval, overwriteStatus bool) (*PortalApproval, error) {
	now := time.Now().Unix()
	if item.CreatedTS == 0 {
		item.CreatedTS = now
	}
	if item.LastSeenTS == 0 {
		item.LastSeenTS = now
	}
	if item.Status == "" {
		item.Status = PortalApprovalPending
	}
	if item.ApprovalID == 0 {
		if err := q.db.QueryRow(ctx, getNextPortalApprovalIDQuery).Scan(&item.ApprovalID); err != nil {
			return nil, err
		}
	}
	query := upsertPortalApprovalQuery
	if overwriteStatus {
		query = upsertPortalApprovalWithStatusQuery
	}
	_, err := q.db.Exec(
		ctx, query,
		item.ApprovalID, item.UserID, item.PortalID, item.PortalReceiver, item.PeerType,
		item.EntityID, item.TopicID, item.Title, item.Username, item.Status,
		item.LastEvent, item.CreatedTS, item.LastSeenTS,
	)
	if err != nil {
		return nil, err
	}
	return q.GetByPortal(ctx, item.UserID, item.PortalID, item.PortalReceiver)
}

func (q *PortalApprovalQuery) GetByPortal(ctx context.Context, userID int64, portalID networkid.PortalID, receiver networkid.UserLoginID) (*PortalApproval, error) {
	item, err := portalApprovalScanner(q.db.QueryRow(ctx, getPortalApprovalByPortalQuery, userID, portalID, receiver))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &item, err
}

func (q *PortalApprovalQuery) GetByID(ctx context.Context, userID, approvalID int64) (*PortalApproval, error) {
	item, err := portalApprovalScanner(q.db.QueryRow(ctx, getPortalApprovalByIDQuery, userID, approvalID))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &item, err
}

func (q *PortalApprovalQuery) GetByStatus(ctx context.Context, userID int64, status PortalApprovalStatus) ([]PortalApproval, error) {
	return portalApprovalScanner.NewRowIter(q.db.Query(ctx, getPortalApprovalByStatusQuery, userID, status)).AsList()
}

func (q *PortalApprovalQuery) SetStatus(ctx context.Context, userID, approvalID int64, status PortalApprovalStatus) error {
	_, err := q.db.Exec(ctx, setPortalApprovalStatusQuery, userID, approvalID, status, time.Now().Unix())
	return err
}

func (q *PortalApprovalQuery) DeletePendingOlderThan(ctx context.Context, userID, cutoffTS int64) (int64, error) {
	res, err := q.db.Exec(ctx, deleteOldPendingPortalApprovalQuery, userID, PortalApprovalPending, cutoffTS)
	if err != nil {
		return 0, err
	}
	count, err := res.RowsAffected()
	if err != nil {
		return 0, nil
	}
	return count, nil
}
