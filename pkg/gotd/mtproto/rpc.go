package mtproto

import (
	"context"

	"github.com/go-faster/errors"
	"go.uber.org/zap"

	"go.mau.fi/mautrix-telegram/pkg/gotd/bin"
	"go.mau.fi/mautrix-telegram/pkg/gotd/mt"
	"go.mau.fi/mautrix-telegram/pkg/gotd/proto"
	"go.mau.fi/mautrix-telegram/pkg/gotd/rpc"
)

// Invoke sends input and decodes result into output.
//
// NOTE: Assuming that call contains content message (seqno increment).
func (c *Conn) Invoke(ctx context.Context, input bin.Encoder, output bin.Decoder) error {
	msgID, seqNo := c.nextMsgSeq(true)
	req := rpc.Request{
		MsgID:  msgID,
		SeqNo:  seqNo,
		Input:  input,
		Output: output,
	}

	for retries := 0; ; retries++ {
		var badMsgErr *badMessageError
		err := c.rpc.Do(ctx, req)
		if err == nil || retries >= 2 || !errors.As(err, &badMsgErr) {
			return err
		} else if badMsgErr.Code == codeIncorrectServerSalt {
			// Store salt from server.
			c.storeSalt(badMsgErr.NewSalt)
			// Reset saved salts to fetch new.
			c.salts.Reset()
			c.log.Info("Retrying request after updating salt from badMsgErr", zap.Int64("msg_id", req.MsgID))
		} else if badMsgErr.TimeResynced {
			req.MsgID, req.SeqNo = c.nextMsgSeq(true)
			c.log.Info("Retrying request after adjusting time offset from badMsgErr",
				zap.Int64("old_msg_id", msgID),
				zap.Int64("new_msg_id", req.MsgID),
				zap.Stringer("old_msg_id_str", proto.MessageID(msgID)),
				zap.Stringer("new_msg_id_str", proto.MessageID(req.MsgID)),
			)
		} else {
			return err
		}
	}
}

func (c *Conn) dropRPC(req rpc.Request) error {
	ctx, cancel := context.WithTimeout(context.Background(),
		c.getTimeout(mt.RPCDropAnswerRequestTypeID),
	)
	defer cancel()

	var resp mt.RPCDropAnswerBox
	if err := c.Invoke(ctx, &mt.RPCDropAnswerRequest{
		ReqMsgID: req.MsgID,
	}, &resp); err != nil {
		return err
	}

	switch resp.RpcDropAnswer.(type) {
	case *mt.RPCAnswerDropped, *mt.RPCAnswerDroppedRunning:
		return nil
	case *mt.RPCAnswerUnknown:
		return errors.New("answer unknown")
	default:
		return errors.Errorf("unexpected response type: %T", resp.RpcDropAnswer)
	}
}
