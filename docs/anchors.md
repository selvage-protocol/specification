# `vectors/anchors/`

The one artifact here that is not a replayable transcript. A transcript is one server's bytes, and
what `vector 010` cannot show is whether *two different libraries* agree about the shape of the
member inside those bytes, because a vector only ever decodes it with the library that wrote it.
So `vectors/anchors/` holds one document and one caret, written the way each of the two
ecosystems' libraries writes it (the yjs shape, which names the scope beside the element, and the
`yrs` shape, which names the element alone), together with the document both anchors are taken
from. [`test/crossing.test.ts`](https://github.com/selvage-protocol/vscode_client/blob/main/test/crossing.test.ts)
rebuilds the `yjs` half from the real library and resolves the `yrs` half, so a shape one side stops
accepting is a red test rather than a peer that silently shows no cursor. The file's own `notes`
member says the same thing next to the data.
