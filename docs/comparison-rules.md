# Comparison rules

Four are worth knowing before writing a vector, because the runner enforces them:

- **Member sets are exact, in both directions.** A frame with a member the vector does not
  mention fails. A member-locked vector is checking that nothing was silently added or renamed,
  so an implementation that adds a member to a frame has broken the version the frame is of.
- **`peers`, `capabilities` and `wire_versions` are compared as sets.** The protocol
  promises no order for them (`CANONICAL.md` §2.7), so the comparison matches them as multisets
  and puts the vector's into the order the wire sent before it compares the bytes. The sealed
  room state's listing is not among them: the protocol promises an order for it (`PROTOCOL.md`
  §7.1), and the comparison holds it to the order the vector wrote.
- **The bytes are compared, not just the parsed JSON.** The vector's frame is written in the
  canonical form of `CANONICAL.md`, the reference server produces exactly those bytes, and a
  failure prints both.
- **Binary frames are compared byte for byte.** The server is payload-opaque, so its whole job on
  the document and awareness paths is to deliver what it was handed.
