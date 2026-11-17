# Graph schema version 1

The indexer writes this schema. Re-indexing a repository deletes that repository's previous nodes (everything with its `repo_id` except the `Repository` node) and merges the new payload. Identifiers are deterministic SHA-256 prefixes, so the same path and qualified name keep the same `id`.

`SchemaMeta` records `{id: "codebase-kg", version: 1}`. It is not tied to a repository.

## Nodes

| Label | Identity | Purpose |
| --- | --- | --- |
| `Repository` | resolved absolute root path | One indexed tree and its summary counts |
| `File` | repository + relative POSIX path | A `.py` file, including files that failed to parse |
| `Entity` + `Module` | repository + kind + qualified name | One module per successfully parsed file |
| `Entity` + `Class` | same | Class definition |
| `Entity` + `Function` | same | Function, nested function, or lambda |
| `Entity` + `Method` | same | Function defined directly in a class body |
| `Import` | repository + file + location + imported name | One imported alias, including external imports |
| `CallSite` | caller + line + ordinal + raw text | Every call expression |
| `ParseError` | path + message | Syntax error that did not stop the scan |
| `SkippedPath` | path + reason | Ignored, unreadable, oversized, or escaped symlink |
| `SearchChunk` | repository + entity | Text and embedding used for Q&A |
| `SchemaMeta` | constant id `codebase-kg` | Schema version and embedding dimensions |

Every code definition uses the `Entity` label plus its specific label. Qualified names follow Python's `__qualname__` shape, prefixed by the module name. Nested functions include `.<locals>.`. A second definition with the same qualified name in one file gets an `@L<line>` suffix.

## Relationships

| Type | From | To | Meaning |
| --- | --- | --- | --- |
| `CONTAINS` | `Repository` | `File` | The repository contains the file |
| `CONTAINS` | `Class` | `Method` | The class body defines the method |
| `DEFINES` | `File` | `Entity` | The file defines the entity |
| `DEFINES` | `Module`, `Function`, or `Method` | child `Entity` | Lexical parent defines the child |
| `INHERITS` | `Class` | `Class` | A base class resolved inside this repository |
| `HAS_IMPORT` | `Module` | `Import` | The import statement was recorded |
| `IMPORTS` | `Module` | `Module` or `Entity` | The target exists in this repository |
| `CALL_SITE` | caller `Entity` | `CallSite` | A call was seen. This includes unresolved and builtin calls |
| `CALLS` | caller `Entity` | callee `Entity` | The call was resolved to an in-repository entity |
| `HAS_PARSE_ERROR` | `File` | `ParseError` | The file could not be parsed |
| `DESCRIBES` | `SearchChunk` | `Entity` | The chunk embeds that entity |

`CALLS` is written only when resolution succeeds. Builtin calls such as `print` and dynamic calls such as `getattr(...)()` or `obj.method()` on a local variable become `CallSite` nodes with `status` `builtin` or `unresolved`. They do not point at a guessed callee.

External imports still produce an `Import` node. They get an `IMPORTS` edge only when the target module or entity was indexed.

## Indexes

Uniqueness constraints exist on `id` for every label above. Lookup indexes cover repository id, entity kind, entity name, file path, chunk repository, call-site repository, and import module.

When Neo4j accepts it, a cosine vector index named `ckg_chunk_embedding` covers `SearchChunk.embedding`. The dimension comes from `CKG_OPENAI_EMBEDDING_DIMENSIONS` (default 1536). If the server cannot create that index, embeddings remain on the node and similarity is computed in the API process for that repository.

## What is not in the graph

Full source listings are not stored. Search chunks may include a truncated excerpt only after an explicit embedding request. Secrets from the environment are not graph properties.
