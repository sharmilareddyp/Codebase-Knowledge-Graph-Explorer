# Codebase Knowledge Graph Explorer

Index one or more local Python repositories, store the extracted entities in Neo4j, and explore them through a typed HTTP API or a grounded natural-language question.

The indexer uses Python's `ast` module. It does not import or execute the code it reads. Call edges are best-effort: dynamic calls stay unresolved instead of being guessed. This project is a local exploration tool, not a complete call-graph product and not a production service.

## Architecture

- `codebase_kg.indexer` finds `.py` files, parses them, and resolves the calls it can prove.
- `codebase_kg.graph` serializes a scan into schema version 1 and writes it with parameterized Cypher. An in-memory store implements the same read API for tests.
- `codebase_kg.retrieval` builds one text chunk per entity, embeds it with LangChain's OpenAI embeddings adapter, and asks a chat model to answer from the retrieved graph context.
- `codebase_kg.api` is a FastAPI app. It does not expose a Cypher endpoint.
- `ckg` is the command line: `index`, `check-neo4j`, and `serve`.

Schema version 1 is documented in [docs/graph-schema.md](docs/graph-schema.md).

## Design choices

- Repository ids are a hash of the resolved absolute path. Re-indexing the same path updates existing nodes. Moving the directory creates a new repository id and leaves the old graph in place.
- Qualified names follow Python's `__qualname__` form, prefixed with the module path. Nested functions include `.<locals>.`.
- A resolved call becomes a `CALLS` relationship. An unresolved or builtin call becomes a `CallSite` and does not point at a guessed target. Calling a class points at the class, not implicitly at `__init__`.
- `self.method()` / `cls.method()` can resolve to a method on that class or on a base class that was itself resolved inside the repository. Calls on ordinary local variables do not.
- Only module-level imports are used for name binding. Star imports, function-local imports, `super()`, `getattr`, and other computed call targets stay unresolved.
- `.gitignore` rules are applied where practical, relative to the file that declared them. A child `.gitignore` cannot pull files back out of a directory that was already skipped. Default skipped directory names include `.git`, `venv`, `.venv`, `build`, `dist`, `__pycache__`, and `site-packages`.
- The HTTP API indexes a path only when it is inside `CKG_ALLOWED_ROOTS`. The CLI indexes the directories you pass and does not use that list.
- Embeddings are optional. Neo4j 5.26 stores them on `SearchChunk` nodes and uses a cosine vector index when the server accepts it. If the index cannot be created, the same stored vectors are ranked in process for that repository. Re-indexing without `--embed` clears previous chunks for that repository.
- Answers cite the retrieved records (repository, file, entity, line range). The model is instructed to say when the evidence is insufficient. Citations are the retrieved evidence, not a parse of the model's prose.
- Source excerpts are sent to OpenAI only when you request embeddings or a natural-language answer. Do not point that step at repositories you cannot send to the embedding provider.

## Requirements

- Python 3.11 or newer
- Docker, if you want the bundled Neo4j database
- An OpenAI API key, only for embeddings and `/query`

## Setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env
docker compose up -d
```

The compose file starts Neo4j 5.26 with user `neo4j`, password `please-change-me`, and a persistent `neo4j_data` volume. Change that password in both `docker-compose.yml` and `.env` before using this beyond a local demo. The browser UI is at `http://localhost:7474` and Bolt is `bolt://localhost:7687`.

Wait until the container is healthy, then check the driver:

```bash
ckg check-neo4j
```

## Configuration

| Variable | Purpose |
| --- | --- |
| `CKG_NEO4J_URI` | Bolt URI. Default `bolt://localhost:7687`. |
| `CKG_NEO4J_USER` | Database user. |
| `CKG_NEO4J_PASSWORD` | Database password. |
| `CKG_NEO4J_DATABASE` | Database name. Default `neo4j`. |
| `OPENAI_API_KEY` or `CKG_OPENAI_API_KEY` | Required for embeddings and `/query`. Leave empty to index and browse only. |
| `CKG_OPENAI_EMBEDDING_MODEL` | Default `text-embedding-3-small`. |
| `CKG_OPENAI_EMBEDDING_DIMENSIONS` | Default `1536`. Must match the vector index. |
| `CKG_OPENAI_CHAT_MODEL` | Default `gpt-4o-mini`. |
| `CKG_ALLOWED_ROOTS` | Comma-separated absolute directories the API may index. Empty means the API refuses every index request. |
| `CKG_LOG_LEVEL` | Default `INFO`. Logs include counts and paths, not source listings or secrets. |
| `CKG_API_HOST` | Default `127.0.0.1`. |
| `CKG_API_PORT` | Default `8000`. |
| `CKG_VECTOR_MIN_SCORE` | Minimum cosine score for a chunk to count as evidence. Default `0.20`. |

## Index the sample repository

`examples/sample_repo` is a small package with classes, imports, a resolved `self` call, an unresolved instance call, a builtin `getattr`, a gitignored file, skipped `build/` and `venv/` directories, and one file with a syntax error.

```bash
ckg index examples/sample_repo
```

The command prints a repository id. Add embeddings when a key is configured:

```bash
ckg index examples/sample_repo --embed
```

Useful flags: `--ignore-dir NAME`, `--no-gitignore`, `--follow-symlinks`, `--max-file-bytes N`. Symlinks are skipped unless `--follow-symlinks` is set, and a symlink that resolves outside the repository is always skipped.

## Run the API

Set the allow-list to the directory that contains the sample, then start the server:

```bash
export CKG_ALLOWED_ROOTS="$(pwd)/examples"
ckg serve
```

Interactive docs are at `http://127.0.0.1:8000/docs`.

```bash
curl -s http://127.0.0.1:8000/health

curl -s -X POST http://127.0.0.1:8000/repositories/index \
  -H 'content-type: application/json' \
  -d "{\"paths\":[\"$(pwd)/examples/sample_repo\"],\"embed\":false}"

curl -s http://127.0.0.1:8000/repositories

curl -s "http://127.0.0.1:8000/repositories/REPOSITORY_ID/entities?kind=function&name=slugify"

curl -s http://127.0.0.1:8000/entities/ENTITY_ID

curl -s http://127.0.0.1:8000/entities/ENTITY_ID/callers

curl -s http://127.0.0.1:8000/entities/ENTITY_ID/callees
```

Natural-language query (requires embeddings for that repository and an API key):

```bash
curl -s -X POST http://127.0.0.1:8000/query \
  -H 'content-type: application/json' \
  -d '{"repository_id":"REPOSITORY_ID","question":"What does slugify do?","top_k":5}'
```

`/query` returns `sufficient_evidence: false` and an explicit answer when the retrieved chunks do not support a claim. `limitations` always describes the static-analysis gap. A path outside `CKG_ALLOWED_ROOTS` is rejected with HTTP 403.

## Tests

Unit and API tests use an in-memory graph and fake embedders. They do not need Neo4j or OpenAI.

```bash
pytest
```

Integration tests are marked `integration` and are excluded by default. With Neo4j running and `.env` configured:

```bash
CKG_RUN_INTEGRATION=1 pytest -m integration
```

## Known limitations

- No type inference. `user.rename()` is unresolved even when `user` was just constructed as `User`.
- Inheritance linking is a parent walk over in-repository bases with simple names, not Python's C3 MRO. External bases such as `Exception` are stored as text and do not produce an edge.
- Decorators are recorded but not executed, so a decorator that replaces a function is invisible.
- Lambdas are separate function entities named with their line and column. Duplicate qualified names in one file are disambiguated with the line number.
- Docstrings stored on entities are truncated to 4000 characters. Embedding excerpts are truncated to 1500 characters. Files larger than `max_file_bytes` (default 1_000_000) are skipped.
- The vector index is global. Queries over-fetch and filter by repository, then fill any shortfall by scanning that repository's stored embeddings. Large repositories will spend memory on that fallback.
- Re-indexing replaces one repository's graph. A crash during a write can leave that repository partial until the next index.
- Answers are only as good as the retrieved excerpts. The tool will not invent a citation when nothing was retrieved, and it should not be treated as proof of runtime behavior.
# Document-Ingestion-Retrieval
