// mautrix-telegram - A Matrix-Telegram puppeting bridge.
// Copyright (C) 2026 Tulir Asokan
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.

package connector

import (
	"context"
	"fmt"
	"strconv"
	"strings"

	"maunium.net/go/mautrix/bridgev2/commands"
	"maunium.net/go/mautrix/bridgev2/networkid"
	"maunium.net/go/mautrix/format"
	"maunium.net/go/mautrix/id"

	"go.mau.fi/mautrix-telegram/pkg/connector/ids"
)

type cleanupUserCandidate struct {
	ID    networkid.UserID
	MXID  id.UserID
	Name  string
	IsBot bool
}

const (
	countUnusedGhostUsersQuery = `
		SELECT COUNT(*)
		FROM ghost AS g
		WHERE g.bridge_id=$1
		  AND g.id<>''
		  AND g.id NOT LIKE 'channel-%'
		  AND NOT EXISTS (SELECT 1 FROM user_login AS ul WHERE ul.bridge_id=g.bridge_id AND ul.id=g.id)
		  AND NOT EXISTS (SELECT 1 FROM portal AS p WHERE p.bridge_id=g.bridge_id AND p.other_user_id=g.id)
		  AND NOT EXISTS (SELECT 1 FROM message AS m WHERE m.bridge_id=g.bridge_id AND m.sender_id=g.id)
		  AND NOT EXISTS (SELECT 1 FROM reaction AS r WHERE r.bridge_id=g.bridge_id AND r.sender_id=g.id)
	`
	listUnusedGhostUsersQuery = `
		SELECT g.id, g.name, g.is_bot
		FROM ghost AS g
		WHERE g.bridge_id=$1
		  AND g.id<>''
		  AND g.id NOT LIKE 'channel-%'
		  AND NOT EXISTS (SELECT 1 FROM user_login AS ul WHERE ul.bridge_id=g.bridge_id AND ul.id=g.id)
		  AND NOT EXISTS (SELECT 1 FROM portal AS p WHERE p.bridge_id=g.bridge_id AND p.other_user_id=g.id)
		  AND NOT EXISTS (SELECT 1 FROM message AS m WHERE m.bridge_id=g.bridge_id AND m.sender_id=g.id)
		  AND NOT EXISTS (SELECT 1 FROM reaction AS r WHERE r.bridge_id=g.bridge_id AND r.sender_id=g.id)
		ORDER BY g.id
		LIMIT $2
	`
	deleteGhostUserQuery = "DELETE FROM ghost WHERE bridge_id=$1 AND id=$2"
)

var cmdCleanupUsers = &commands.FullHandler{
	Func: fnCleanupUsers,
	Name: "cleanup-users",
	Help: commands.HelpMeta{
		Section:     commands.HelpSectionAdmin,
		Description: "Find or delete unused Telegram ghost user records",
		Args:        "[--delete] [--limit N]",
	},
	RequiresAdmin: true,
}

func fnCleanupUsers(ce *commands.Event) {
	deleteMode, limit, ok := parseCleanupUsersArgs(ce)
	if !ok {
		return
	}
	candidates, total, err := getCleanupUserCandidates(ce, limit)
	if err != nil {
		ce.Log.Err(err).Msg("Failed to list unused Telegram ghost users")
		ce.Reply("Failed to list unused Telegram ghost users: %v", err)
		return
	} else if total == 0 {
		ce.Reply("No unused Telegram ghost users found.")
		return
	}

	if deleteMode {
		deleted, err := deleteCleanupUserCandidates(ce, candidates)
		if err != nil {
			ce.Log.Err(err).Msg("Failed to delete unused Telegram ghost users")
			ce.Reply("Deleted %d unused Telegram ghost records, then failed: %v", deleted, err)
			return
		}
		ce.Reply(
			"Deleted %d unused Telegram ghost records from the bridge database.\n\n"+
				"Note: this command does not purge Synapse user rows or media files; it only removes bridge-side records that have no portals, messages, reactions or login.",
			deleted,
		)
		return
	}

	var builder strings.Builder
	fmt.Fprintf(
		&builder,
		"Found %d unused Telegram ghost users. Showing %d.\n\n"+
			"These records have no Telegram login, no DM portal, no bridged messages and no reactions.\n"+
			"Run `$cmdprefix cleanup-users --delete` to delete the listed bridge-side records.\n\n",
		total, len(candidates),
	)
	for i, candidate := range candidates {
		fmt.Fprintf(&builder, "%d\\. **%s**\n", i+1, format.EscapeMarkdown(cleanupUserDisplayName(candidate)))
		fmt.Fprintf(&builder, "   mxid: `%s`\n", candidate.MXID)
		fmt.Fprintf(&builder, "   tg id: `%s`\n", candidate.ID)
		if candidate.IsBot {
			builder.WriteString("   bot: yes\n\n")
		} else {
			builder.WriteString("   bot: no\n\n")
		}
	}
	if total > len(candidates) {
		fmt.Fprintf(&builder, "Use `$cmdprefix cleanup-users --limit %d` to show more.\n", min(total, 200))
	}
	ce.Reply(builder.String())
}

func parseCleanupUsersArgs(ce *commands.Event) (deleteMode bool, limit int, ok bool) {
	limit = 20
	ok = true
	for i := 0; i < len(ce.Args); i++ {
		switch ce.Args[i] {
		case "--delete":
			deleteMode = true
		case "--limit":
			i++
			if i >= len(ce.Args) {
				ce.Reply("Usage: `$cmdprefix cleanup-users [--delete] [--limit N]`")
				return false, 0, false
			}
			parsed, err := strconv.Atoi(ce.Args[i])
			if err != nil || parsed <= 0 {
				ce.Reply("Invalid limit: %s", format.SafeMarkdownCode(ce.Args[i]))
				return false, 0, false
			}
			limit = parsed
		default:
			ce.Reply("Usage: `$cmdprefix cleanup-users [--delete] [--limit N]`")
			return false, 0, false
		}
	}
	if limit > 200 {
		limit = 200
	}
	return deleteMode, limit, true
}

func getCleanupUserCandidates(ce *commands.Event, limit int) ([]cleanupUserCandidate, int, error) {
	bridgeID := ce.Bridge.DB.BridgeID
	var total int
	err := ce.Bridge.DB.QueryRow(ce.Ctx, countUnusedGhostUsersQuery, bridgeID).Scan(&total)
	if err != nil {
		return nil, 0, err
	}
	rows, err := ce.Bridge.DB.Query(ce.Ctx, listUnusedGhostUsersQuery, bridgeID, limit)
	if err != nil {
		return nil, 0, err
	}
	defer rows.Close()

	candidates := make([]cleanupUserCandidate, 0, min(total, limit))
	for rows.Next() {
		var candidate cleanupUserCandidate
		if err = rows.Scan(&candidate.ID, &candidate.Name, &candidate.IsBot); err != nil {
			return nil, 0, err
		}
		candidate.MXID = ce.Bridge.Matrix.GhostIntent(candidate.ID).GetMXID()
		candidates = append(candidates, candidate)
	}
	return candidates, total, rows.Err()
}

func deleteCleanupUserCandidates(ce *commands.Event, candidates []cleanupUserCandidate) (int, error) {
	deleted := 0
	for _, candidate := range candidates {
		if err := cleanupTelegramGhostIndexes(ce.Ctx, ce.Bridge.Network.(*TelegramConnector), candidate.ID); err != nil {
			return deleted, err
		}
		if _, err := ce.Bridge.DB.Exec(ce.Ctx, deleteGhostUserQuery, ce.Bridge.DB.BridgeID, candidate.ID); err != nil {
			return deleted, err
		}
		deleted++
	}
	return deleted, nil
}

func cleanupTelegramGhostIndexes(ctx context.Context, tc *TelegramConnector, userID networkid.UserID) error {
	peerType, entityID, err := ids.ParseUserID(userID)
	if err != nil {
		// Corrupt unused ghost rows should not block cleanup of the bridge row itself.
		return nil
	}
	if err = tc.Store.Username.Set(ctx, peerType, entityID, ""); err != nil {
		return fmt.Errorf("failed to clear username index for %s: %w", userID, err)
	}
	if peerType == ids.PeerTypeUser {
		if err = tc.Store.PhoneNumber.Set(ctx, entityID, ""); err != nil {
			return fmt.Errorf("failed to clear phone index for %s: %w", userID, err)
		}
	}
	return nil
}

func cleanupUserDisplayName(candidate cleanupUserCandidate) string {
	if candidate.Name != "" {
		return candidate.Name
	}
	return string(candidate.MXID)
}
