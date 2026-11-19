# Host-run relay

A room does not need a separately deployed server. The host client can run the relay
for its own room. The relay still speaks the session layer in
[`PROTOCOL.md`](../PROTOCOL.md): it mints the room and token, assigns `peer_id`, publishes
membership events and relays sealed binary frames byte for byte. What changes is where
it runs. The relay is a role the host's connection plays, not a service a deployer
operates.

The wire is unchanged. `selvage/2`, `SJ-C/1`, the schemas and the vectors still apply to
every frame. A guest cannot tell from the frames whether the relay is a standalone
process or part of the host's client.

## Reaching the host

A guest needs an address it can dial. The invite carries that address in its base URL,
exactly as it carries a standalone server's address today.

- **Tailscale (recommended).** Install Tailscale on the host and every guest, and join
  them to the same tailnet. The host client reads `tailscale ip -4` and uses that address
  in the invite. Guests dial the invite over the tailnet. No router configuration or
  public port is needed, and the invite keeps working when the host moves between
  networks as long as its tailnet address stays the same.
- **Same LAN.** The client can use the host's LAN address instead. Guests must be on the
  same network. This needs no extra software, but it does not work across networks.
- **Public address.** A host with a reachable address can advertise it directly. A
  router port forward is then the host's own choice, and the invite exposes that address
  to anyone who holds the link.

Every invite leaks the address it carries. Treat the invite as the secret it already is:
the room key, the host public key and now the host's reachable address travel together.

## What stays on the host

The host must stay online for the room. That was already true: only the host writes the
room's text to the working copy, and a client ends its session after the host-away
window in §13.8. A host-run relay makes the same fact visible in the transport. When
the host process ends, the relay ends, `peer.left` reaches the room and the room follows
the grace in §9.

The relay's bounds are the host's bounds. A host applies the finite configured limits
from §2.1 before it seats a peer: frame size, envelope size, queue bytes, peers per
room and inbound budget. A public address needs the same terminator and rate limiting a
deployment needs today. A tailnet keeps the listener off the public internet.

## What does not change

- Sealed frames, signatures, counters and the host-signed room state.
- The CRDT sync exchange and awareness. Convergence is still between peers.
- Roles, holds, the listing and the viewer rule. All are peer-side facts.
- The conformance corpus. The relay emits the same bytes the vectors pin.

## Browser hosting

A page cannot open a listening socket, so a browser tab cannot run the relay. Browser
clients still join a room from an invite. A room that needs a browser-hosted working
copy runs the relay in an editor client or an agent on the same machine as the folder.
