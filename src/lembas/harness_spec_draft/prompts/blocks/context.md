<!-- LLeMbas CLI and LLeMbas, spec 3.0.0: how tool results are kept short (context.md). -->
# Tool results
- Every tool result starts with its id in brackets, like `[#a3f2]`. A read's bracket also says the path, the lines shown, the file's length and a short hash of its content: `[#a3f2 src/app.ts lines 1-200 of 340 sha 1c9e04ab]`.
- Reading a file that has not changed since an earlier read in this conversation returns a stub instead of the lines: `Unchanged since #a3f2, which already shows lines 1-200.` The content is in that earlier result. Use it; do not read the file again to see it.
- A listing or search repeated with the same result returns `Same result as #f001.` in the same way.
- Long output is shortened to its beginning and its end, with `[1234 lines elided, full output #c9d0]` between them.
- `recall` with an id shows that stored result in full, or from a line on: for lines left out of a shortened output, or for a result from before the conversation was summarised. Nothing is lost; recall only what you need.
- An edit answers with the diff of the change, not the whole file. The earlier read with that diff applied is the file now: read it again only for lines you have not seen.
- After the conversation is summarised, earlier results are no longer in front of you. The summary ends with a table of the files read before it, with their ids; a file read again then comes back in full.
