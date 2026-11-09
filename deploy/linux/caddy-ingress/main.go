package main

import (
	"io"
	"net"
	"sync"
	"time"

	"github.com/caddyserver/caddy/v2"
	"github.com/caddyserver/caddy/v2/caddyconfig/caddyfile"
	caddycmd "github.com/caddyserver/caddy/v2/cmd"
	_ "github.com/caddyserver/caddy/v2/modules/standard"
	"github.com/mholt/caddy-l4/layer4"
	_ "github.com/mholt/caddy-l4/modules/l4tls"
)

func main() {
	caddycmd.Main()
}

func init() {
	caddy.RegisterModule(relay{})
}

// relay preserves the former ingress's TCP dial and bidirectional inactivity
// limits. TLS remains encrypted until it reaches the existing Caddy edge.
type relay struct {
	Upstream       string         `json:"upstream"`
	ConnectTimeout caddy.Duration `json:"connect_timeout"`
	IdleTimeout    caddy.Duration `json:"idle_timeout"`
}

func (relay) CaddyModule() caddy.ModuleInfo {
	return caddy.ModuleInfo{
		ID:  "layer4.handlers.switch_relay",
		New: func() caddy.Module { return new(relay) },
	}
}

func (r *relay) UnmarshalCaddyfile(d *caddyfile.Dispenser) error {
	d.Next()
	var connect, idle string
	if !d.AllArgs(&r.Upstream, &connect, &idle) {
		return d.ArgErr()
	}
	for _, arg := range []struct {
		value string
		field *caddy.Duration
	}{{connect, &r.ConnectTimeout}, {idle, &r.IdleTimeout}} {
		value, err := caddy.ParseDuration(arg.value)
		if err != nil || value <= 0 {
			return d.Errf("invalid timeout %q", arg.value)
		}
		*arg.field = caddy.Duration(value)
	}
	return nil
}

func (r *relay) Handle(down *layer4.Connection, _ layer4.Handler) error {
	up, err := net.DialTimeout("tcp4", r.Upstream, time.Duration(r.ConnectTimeout))
	if err != nil {
		return err
	}
	relayConnection(down, up, time.Duration(r.IdleTimeout))
	return nil
}

func relayConnection(down, up net.Conn, timeout time.Duration) {
	var deadlineMu sync.Mutex
	refreshDeadline := func() {
		deadlineMu.Lock()
		defer deadlineMu.Unlock()
		deadline := time.Now().Add(timeout)
		_ = down.SetDeadline(deadline)
		_ = up.SetDeadline(deadline)
	}

	refreshDeadline()
	finished := make(chan struct{}, 2)
	copyTraffic := func(dst, src net.Conn) {
		// Hide WriterTo/ReaderFrom so the idle timer sees successful writes.
		_, _ = io.Copy(activityWriter{dst, refreshDeadline}, struct{ io.Reader }{src})
		finished <- struct{}{}
	}
	go copyTraffic(up, down)
	go copyTraffic(down, up)
	<-finished
	_ = down.Close()
	_ = up.Close()
	<-finished
}

type activityWriter struct {
	io.Writer
	refresh func()
}

func (w activityWriter) Write(data []byte) (int, error) {
	n, err := w.Writer.Write(data)
	if n > 0 {
		w.refresh()
	}
	return n, err
}
