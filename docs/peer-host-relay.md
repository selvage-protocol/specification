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
  them to the same tailnet. Run `tailscale serve` in front of the relay and advertise the
  device's fully qualified tailnet DNS name and Serve's TLS port. Its `wss://` URL is what
  an https guest page can dial (§12), and the certificate is for that DNS name, not for a
  numeric `tailscale ip -4` address. No router configuration or public port is needed, and
  the invite keeps working when the host moves between networks while its tailnet name stays
  the same.
- **Same LAN.** The client can use the host's LAN address instead. Guests must be on the
  same network. This needs no extra software, but it does not work across networks.
- **Public address.** A host with a reachable address can advertise it directly. A
  router port forward is then the host's own choice, and the invite exposes that address
  to anyone who holds the link.

Every invite leaks the address it carries. Treat the invite as the secret it already is:
the room key, the host public key and now the host's reachable address travel together.

## What stays on the host

The host must stay online for the room. That was already true: only the host writes the
room's text to the working copy. When the relay runs in the host's own process, the host's
departure closes every guest's socket with it. A guest reconnects under §9.1 and finds
either a refused connection or `room_unknown`, because the room was relay state and died
with the relay. There is no `peer.left` and no grace to run after the relay is gone. A
relay that outlives the host's session, such as a standalone relay or a separate companion,
leaves the roster to report the host's absence; clients then run the host-away window in
§13.8.

The relay's bounds are the host's bounds. A host applies the finite configured limits
from §2.1 on every connection, before and after it seats a peer: frame size, envelope size, queue bytes, peers per
room and inbound budget, and serves `GET /meta` on the same listener (§2). A relay reachable
by anyone else needs a TLS terminator and rate limiting, whether the address it advertises
is public, a tailnet address or a LAN address (§12). A browser guest on an https page dials
the `wss://` form of that address; an http page may dial `ws://`.

## What does not change

- Sealed frames, signatures, counters and the host-signed room state.
- The CRDT sync exchange and awareness. Convergence is still between peers.
- Roles, holds, the listing and the viewer rule. All are peer-side facts.
- The conformance corpus. The relay emits the same bytes the vectors pin.

## Browser hosting

A page cannot open a listening socket, so a browser tab cannot run the relay. Browser
clients join a room from an invite. The relay runs in a host process on the machine that
holds the working copy, in an editor client or through the MCP server.
