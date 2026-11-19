# talkviz

Talk to your data and see it charted. Ask a question in plain language;
talkviz writes a read-only SQL query, checks it, runs it, picks a chart, and
answers in a sentence or two.

> Early development. The query engine and command line work today. The web
> interface and voice are not built yet.

## How it stays grounded

Every step is checked against the one before it:

1. A **manifest** describes the tables, columns, and caveats of your data.
   The model sees the manifest and nothing else.
2. The model's SQL must be a single `SELECT` over the manifest's tables.
3. DuckDB plans the query without running it, which checks names and types.
4. The query runs on a read-only connection with file and network access
   off, a row limit, and a timeout.
5. The chart spec must fit the columns the query returned.
6. Every number in the reply must appear in the query results.

When a check fails, the reason goes back to the model and it tries again.

## Setup

```sh
uv sync
cp .env.example .env    # then add your OpenRouter key
```

`OPENROUTER_API_KEY` is required. `TALKVIZ_MODEL` picks the model (any
OpenRouter model id); the default is `anthropic/claude-opus-5.5`.

## Use

Register a DuckDB database with the manifest that describes it, then ask:

```sh
uv run talkviz add path/to/data.duckdb path/to/manifest.yaml
uv run talkviz ask "How many orders were there each month?"
uv run talkviz ask          # no question: start a conversation
```

In a conversation you can refer back to charts: "make that a bar chart",
"only since 2020", "show the first one again".

## Manifest

```yaml
name: shop
title: Shop orders
description: Orders placed at a small shop.
dialect: duckdb
data_version_sql: SELECT max(ordered) FROM orders
caveats:
  - Refunds are not included.
tables:
  - name: orders
    description: One row per order.
    columns:
      - name: category
        type: VARCHAR
        description: Product category.
        values: [books, games, toys]   # optional: every value it can hold
examples:
  - question: How many orders?
    sql: SELECT count(*) FROM orders
```

Answers are only as good as the manifest. Describe each column in plain
words and state the traps in `caveats`; the reply will mention them.

## Scoring a model

An eval file is a YAML list of questions with a query that answers each
correctly. A model passes a question when its query returns the same data.

```sh
uv run talkviz eval path/to/eval.yaml --model anthropic/claude-haiku-5.5
```

## Development

```sh
uv sync
uv run pre-commit install   # once per clone
uv run pytest
uv run ruff check . && uv run ruff format --check . && uv run ty check
```

The hooks run ruff, ty, and a lockfile check on commit, and the tests on
push. CI runs the same checks on every pull request.

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org)
(`feat: ...`, `fix: ...`, `docs: ...`); a hook checks the format. Releases are
automated from them: on each push to `main`, release-please updates a release
pull request with the next version and changelog. `fix` bumps the patch
version, `feat` the minor, and `feat!` marks a breaking change. Merging that
pull request tags the release and publishes it to PyPI.
