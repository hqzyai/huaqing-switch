package main

import (
	"io"
	"net"
	"testing"
	"time"

	"github.com/caddyserver/caddy/v2/caddyconfig/caddyfile"
)

func TestRelayCaddyfile(t *testing.T) {
	var handler relay
	if err := handler.UnmarshalCaddyfile(caddyfile.NewTestDispenser("switch_relay edge:8443 5s 330s")); err != nil {
		t.Fatal(err)
	}
	if handler.Upstream != "edge:8443" || time.Duration(handler.ConnectTimeout) != 5*time.Second ||
		time.Duration(handler.IdleTimeout) != 330*time.Second {
		t.Fatalf("unexpected relay configuration: %+v", handler)
	}
	if err := handler.UnmarshalCaddyfile(caddyfile.NewTestDispenser("switch_relay edge:8443 5s 0s")); err == nil {
		t.Fatal("zero idle timeout must be rejected")
	}
}

func TestRelayBidirectionalTrafficAndIdleClosure(t *testing.T) {
	client, down := net.Pipe()
	up, server := net.Pipe()
	t.Cleanup(func() {
		_ = client.Close()
		_ = server.Close()
	})
	const idle = 600 * time.Millisecond
	finished := make(chan struct{})
	go func() {
		relayConnection(down, up, idle)
		close(finished)
	}()
	assertTraffic := func(sender, receiver net.Conn, payload string) {
		t.Helper()
		_ = sender.SetWriteDeadline(time.Now().Add(idle))
		_ = receiver.SetReadDeadline(time.Now().Add(idle))
		sent := make(chan error, 1)
		go func() {
			_, err := io.WriteString(sender, payload)
			sent <- err
		}()
		got := make([]byte, len(payload))
		if _, err := io.ReadFull(receiver, got); err != nil || string(got) != payload {
			t.Fatalf("received %q, err=%v, want %q", got, err, payload)
		}
		if err := <-sent; err != nil {
			t.Fatal(err)
		}
	}
	time.Sleep(350 * time.Millisecond)
	assertTraffic(client, server, "request")
	time.Sleep(350 * time.Millisecond)
	assertTraffic(server, client, "response")

	select {
	case <-finished:
	case <-time.After(2 * idle):
		t.Fatal("relay did not close idle connection")
	}
}
