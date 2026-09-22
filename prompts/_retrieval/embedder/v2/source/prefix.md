# Embedder Query Instruction — source anchor — v2

Represent this taint-flow analysis query for retrieving related C/C++ source code chunks in a
source-to-sink vulnerability search.

A chunk is relevant when it does at least one of the following relative to the entry point named
in the query: defines a symbol/type/macro that the entry point's tainted parameter uses; is a
caller that passes untrusted data into this entry point; is a callee that the tainted value
reaches after this entry point; or performs the dangerous operation (buffer write, format string,
allocation size, array index, free/realloc) that the tainted value ultimately reaches.

A chunk from an unrelated function or file is not relevant merely because it contains similar
tokens, variable names, or a superficially similar operation.

Do NOT: treat a chunk as relevant merely because it defines or mentions the same function or
API name as the Query.

DO: prefer chunks that contain the Query's concrete file:line (the entry or site named in the
Query). A helper definition that lives in another slice is not a substitute for that site.

