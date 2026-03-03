package mtproto

import (
	"context"
	"io"
	"sync"
	"time"

	"github.com/go-faster/errors"
	"go.uber.org/atomic"
	"go.uber.org/zap"

	"go.mau.fi/mautrix-telegram/pkg/gotd/bin"
	"go.mau.fi/mautrix-telegram/pkg/gotd/clock"
	"go.mau.fi/mautrix-telegram/pkg/gotd/crypto"
	"go.mau.fi/mautrix-telegram/pkg/gotd/exchange"
	"go.mau.fi/mautrix-telegram/pkg/gotd/mtproto/salts"
	"go.mau.fi/mautrix-telegram/pkg/gotd/proto"
	"go.mau.fi/mautrix-telegram/pkg/gotd/rpc"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tdsync"
	"go.mau.fi/mautrix-telegram/pkg/gotd/tmap"
	"go.mau.fi/mautrix-telegram/pkg/gotd/transport"
)

// Handler will be called on received message from Telegram.
type Handler interface {
	OnMessage(b *bin.Buffer) error
	OnSession(session Session) error
}

// MessageIDSource is message id generator.
type MessageIDSource interface {
	New(t proto.MessageType) int64
	Reset()
}

// MessageBuf is message id buffer.
type MessageBuf interface {
	Consume(id int64) bool
}

// Cipher handles message encryption and decryption.
type Cipher interface {
	DecryptFromBuffer(k crypto.AuthKey, buf *bin.Buffer) (*crypto.EncryptedMessageData, error)
	Encrypt(key crypto.AuthKey, data crypto.EncryptedMessageData, b *bin.Buffer) error
}

// Dialer is an abstraction for MTProto transport connection creator.
type Dialer func(ctx context.Context) (transport.Conn, error)

// Conn represents a MTProto client to Telegram.
type Conn struct {
	dcID int

	dialer        Dialer
	conn          transport.Conn
	handler       Handler
	rpc           *rpc.Engine
	rsaPublicKeys []exchange.PublicKey
	types         *tmap.Map

	// Wrappers for external world, like current time, logs or PRNG.
	// Should be immutable.
	clock        clock.Clock
	rand         io.Reader
	cipher       Cipher
	log          *zap.Logger
	messageID    MessageIDSource
	messageIDBuf MessageBuf // replay attack protection

	// use session() to access authKey, salt or sessionID.
	sessionMux sync.RWMutex
	authKey    crypto.AuthKey
	salt       int64
	sessionID  int64

	serverTimeOffset time.Duration

	// server salts fetched by getSalts.
	salts salts.Salts

	// sentContentMessages is count of created content messages, used to
	// compute sequence number within session.
	sentContentMessages int32
	reqMux              sync.Mutex

	// ackSendChan is queue for outgoing message id's that require waiting for
	// ack from server.
	ackSendChan  chan int64
	ackBatchSize int
	ackInterval  time.Duration

	// callbacks for ping results.
	// Key is ping id.
	ping    map[int64]chan struct{}
	pingMux sync.Mutex
	// pingTimeout sets ping_delay_disconnect delay.
	pingTimeout time.Duration
	// pingInterval is duration between ping_delay_disconnect request.
	pingInterval time.Duration
	pingCallback func()

	// gotSession is a signal channel for wait for handleSessionCreated message.
	gotSession *tdsync.Ready

	// exchangeLock locks write calls during key exchange.
	exchangeLock sync.RWMutex

	// compressThreshold is a threshold in bytes to determine that message
	// is large enough to be compressed using gzip.
	compressThreshold int
	dialTimeout       time.Duration
	exchangeTimeout   time.Duration
	saltFetchInterval time.Duration
	getTimeout        func(req uint32) time.Duration
	// Ensure Run once.
	ran atomic.Bool
}

// New creates new unstarted connection.
func New(dialer Dialer, opt Options) *Conn {
	// Set default values, if user does not set.
	opt.setDefaults()

	conn := &Conn{
		dcID: opt.DC,

		dialer:       dialer,
		clock:        opt.Clock,
		rand:         opt.Random,
		cipher:       opt.Cipher,
		log:          opt.Logger,
		messageIDBuf: proto.NewMessageIDBuf(100),

		ackSendChan:  make(chan int64),
		ackInterval:  opt.AckInterval,
		ackBatchSize: opt.AckBatchSize,

		rsaPublicKeys: opt.PublicKeys,
		handler:       opt.Handler,
		types:         opt.Types,

		authKey: opt.Key,
		salt:    opt.Salt,

		ping:         map[int64]chan struct{}{},
		pingTimeout:  opt.PingTimeout,
		pingInterval: opt.PingInterval,
		pingCallback: opt.PingCallback,

		gotSession: tdsync.NewReady(),

		rpc:               opt.engine,
		compressThreshold: opt.CompressThreshold,
		dialTimeout:       opt.DialTimeout,
		exchangeTimeout:   opt.ExchangeTimeout,
		saltFetchInterval: opt.SaltFetchInterval,
		getTimeout:        opt.RequestTimeout,
	}
	conn.messageID = proto.NewMessageIDGen(conn.TimeWithOffset)
	if conn.rpc == nil {
		conn.rpc = rpc.New(conn.writeContentMessage, rpc.Options{
			Logger:        opt.Logger.Named("rpc"),
			RetryInterval: opt.RetryInterval,
			MaxRetries:    opt.MaxRetries,
			Clock:         opt.Clock,
			DropHandler:   rpc.NopDrop, // was conn.dropRPC, but disabled for faster shutdown
			OnError:       opt.OnError,
		})
	}

	return conn
}

// handleClose closes rpc engine and underlying connection on context done.
func (c *Conn) handleClose(ctx context.Context) error {
	<-ctx.Done()
	c.log.Info("Connection context done, closing", zap.NamedError("ctx_err", context.Cause(ctx)))

	// Close RPC Engine.
	c.rpc.ForceClose()
	// Close connection.
	if err := c.conn.Close(); err != nil {
		c.log.Debug("Failed to cleanup connection", zap.Error(err))
	}
	c.log.Info("Connection closed")
	return nil
}

var errRunReturned = errors.New("Conn.Run() returned")

// Run initializes MTProto connection to server and blocks until disconnection.
//
// When connection is ready, Handler.OnSession is called.
func (c *Conn) Run(ctx context.Context, f func(ctx context.Context) error) error {
	// Starting connection.
	//
	// This will send initial packet to telegram and perform key exchange
	// if needed.
	if c.ran.Swap(true) {
		return errors.New("do Run on closed connection")
	}

	ctx, cancel := context.WithCancelCause(ctx)
	defer cancel(errRunReturned)

	c.log.Info("Run: start")
	defer c.log.Info("Run: end")
	if err := c.connect(ctx); err != nil {
		return errors.Wrap(err, "start")
	}
	// All goroutines are bound to current call.
	g := tdsync.NewLogGroup(ctx, c.log.Named("group"))
	g.Go("handleClose", c.handleClose)
	g.Go("pingLoop", c.pingLoop)
	g.Go("ackLoop", c.ackLoop)
	g.Go("saltsLoop", c.saltLoop)
	g.Go("userCallback", f)
	g.Go("readLoop", c.readLoop)

	if err := g.Wait(); err != nil {
		return errors.Wrap(err, "group")
	}
	return nil
}

func (c *Conn) setServerTimeOffset(offset time.Duration) {
	if offset == 0 {
		offset = 1
	}
	c.sessionMux.Lock()
	c.serverTimeOffset = offset
	c.sessionMux.Unlock()
	if offset > 10*time.Second || offset < -10*time.Second {
		c.log.Warn("Updated server time offset (high)", zap.Duration("offset", offset))
	} else {
		c.log.Info("Updated server time offset", zap.Duration("offset", offset))
	}
}

func (c *Conn) hasServerTimeOffset() bool {
	c.sessionMux.RLock()
	has := c.serverTimeOffset != 0
	c.sessionMux.RUnlock()
	return has
}

func (c *Conn) TimeWithOffset() (t time.Time) {
	c.sessionMux.RLock()
	t = c.clock.Now().Add(c.serverTimeOffset)
	c.sessionMux.RUnlock()
	return
}

func (c *Conn) altTimeWithOffset() (t time.Time) {
	c.sessionMux.RLock()
	if c.serverTimeOffset != 0 {
		t = c.clock.Now().Add(c.serverTimeOffset)
	}
	c.sessionMux.RUnlock()
	return
}
