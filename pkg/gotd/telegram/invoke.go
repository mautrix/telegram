package telegram

import (
	"context"
	"fmt"
	"strings"

	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/trace"
	"go.uber.org/zap"

	"go.mau.fi/mautrix-telegram/pkg/gotd/bin"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tg"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tgerr"
)

// API returns *tg.Client for calling raw MTProto methods.
func (c *Client) API() *tg.Client {
	return c.tg
}

// Invoke invokes raw MTProto RPC method. It sends input and decodes result
// into output.
func (c *Client) Invoke(ctx context.Context, input bin.Encoder, output bin.Decoder) error {
	if c.tracer != nil {
		spanName := "Invoke"
		var attrs []attribute.KeyValue
		if t, ok := input.(interface{ TypeID() uint32 }); ok {
			id := t.TypeID()
			attrs = append(attrs,
				attribute.Int64("tg.method.id_int", int64(id)),
				attribute.String("tg.method.id", fmt.Sprintf("%x", id)),
			)
			name := c.opts.Types.Get(id)
			if name == "" {
				name = fmt.Sprintf("0x%x", id)
			} else {
				attrs = append(attrs, attribute.String("tg.method.name", name))
			}
			spanName = fmt.Sprintf("Invoke: %s", name)
		}
		spanCtx, span := c.tracer.Start(ctx, spanName,
			trace.WithAttributes(attrs...),
			trace.WithSpanKind(trace.SpanKindClient),
		)
		ctx = spanCtx
		defer span.End()
	}

	return c.invoker.Invoke(ctx, input, output)
}

// invokeDirect directly invokes RPC method, automatically handling datacenter redirects.
func (c *Client) invokeDirect(ctx context.Context, input bin.Encoder, output bin.Decoder) error {
	contextDC, _ := ctx.Value("tg_dc").(*int)

	if _, ok := ctx.Value("tg_dc_inner").(bool); !ok {
		if contextDC != nil && *contextDC > 0 {
			c.log.With(zap.Int("context_dc", *contextDC)).Debug("Invoking on context DC")
			return c.invokeSub(ctx, *contextDC, input, output)
		}
	}

	if err := c.invokeConn(ctx, input, output); err != nil {
		// Handling datacenter migration request.
		if rpcErr, ok := tgerr.As(err); ok && strings.HasSuffix(rpcErr.Type, "_MIGRATE") {
			targetDC := rpcErr.Argument
			log := c.log.With(
				zap.String("error_type", rpcErr.Type),
				zap.Int("target_dc", targetDC),
			)
			// If migration error is FILE_MIGRATE or STATS_MIGRATE, then the method
			// called by authorized client, so we should try to transfer auth to new DC
			// and create new connection.
			if rpcErr.IsOneOf("FILE_MIGRATE", "STATS_MIGRATE") {
				log.Debug("Invoking on target DC")
				if contextDC != nil {
					log.Debug("Setting context DC")
					*contextDC = targetDC
					ctx = context.WithValue(ctx, "tg_dc_inner", true)
				}
				return c.invokeSub(ctx, targetDC, input, output)
			}

			// Otherwise we should change primary DC.
			log.Info("Migrating to target DC")
			return c.invokeMigrate(ctx, targetDC, input, output)
		}

		return err
	}

	return nil
}

// invokeConn directly invokes RPC call on primary connection without any
// additional handling.
func (c *Client) invokeConn(ctx context.Context, input bin.Encoder, output bin.Decoder) error {
	c.connMux.Lock()
	conn := c.conn
	c.connMux.Unlock()

	return conn.Invoke(ctx, input, output)
}
