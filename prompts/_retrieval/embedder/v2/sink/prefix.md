# Embedder Query Instruction — sink anchor — v2

Represent this taint-flow analysis query for retrieving related C/C++ source code chunks in a
source-to-sink vulnerability search.

A chunk is relevant when it does at least one of the following relative to the dangerous
operation named in the query: produces, computes, or passes a value that this operation
consumes; is a caller supplying the argument(s) used at this operation; defines a symbol/type the
operation depends on; or is the untrusted entry point where the value used at this operation
originates.

A chunk from an unrelated function or file is not relevant merely because it contains similar
tokens, variable names, or a superficially similar operation.

Do NOT: treat a chunk as relevant merely because it defines or mentions the same function or
API name as the Query.

DO: prefer chunks that contain the Query's concrete file:line (the call site named in the
Query). A helper definition that lives in another slice is not a substitute for that site.

