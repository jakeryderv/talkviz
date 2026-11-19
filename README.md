# talkviz

Talk to your data and see it charted. Ask a question in plain language;
talkviz writes a read-only SQL query, checks it, runs it, picks a chart, and
answers in a sentence or two.

> Early development. The query engine, command line, and web app (typed
> questions) work today. Voice is not built yet.

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

`OPENROUTER_API_KEY` is required. Two models share the work: one plans the
charts and a smaller, faster one writes the reply, since every number it says
is checked against the results anyway. By default these are
`anthropic/claude-sonnet-5.5` at low reasoning effort and
`anthropic/claude-haiku-5.5` with reasoning off, which together answer in
about 2 to 3 seconds.

| Setting | Flag | What it sets |
|---|---|---|
| `TALKVIZ_MODEL` | `--model` | Planning model: any OpenRouter model id |
| `TALKVIZ_EFFORT` | `--effort` | Its reasoning effort: `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max` |
| `TALKVIZ_SPEECH_MODEL` | `--speech-model` | Reply model |
| `TALKVIZ_SPEECH_EFFORT` | `--speech-effort` | Its reasoning effort |
| `TALKVIZ_ROUTER` | | Router model for the web app, or `off` (default `typesafe/jev-1.14`) |

In the web app, a small decision model looks at each message first. When it
is confident the message only asks to redraw a chart ("make that a donut") or
bring one back ("show the first chart again"), talkviz does that directly in
about a quarter of a second, without the planning or reply models. Everything
else, and any doubt or error, goes to the planner as usual. It uses
OpenRouter's Decisions API, which is in alpha.

The same fast path moves around a chart without changing its data: "zoom in
on 2020 to 2024", "hide Ford", "bring Ford back", "zoom out", "show
everything again". The zoom and hidden series are saved with the chart, like
its view, and a Reset link under the title undoes them.

### Settings

Each chat keeps its own settings: the model that plans, the model that writes
replies, each one's reasoning effort, and whether the fast path is used. A
new chat copies the defaults, which start from the environment variables
above and can be changed in the app (Settings, bottom left, or the line under
the question box). Any OpenRouter model id can be used. Every reply shows
which models produced it and how long it took, so chats run on different
models can be compared. API keys are read from the environment only; the app
shows whether each is set, never its value.

### Voice mode

Switch the question box from Chat to Voice, then hold the space bar and talk;
let go to send. The box shows your voice as you speak. Replies are read
aloud, and talking again cuts a reply short. It needs `ELEVENLABS_API_KEY`
set where talkviz runs, with access to speech to text and text to speech.
Audio goes from the browser to the talkviz server and from there to
ElevenLabs, so the key never reaches the browser.

The voice, speaking speed, language and speech models are part of each
chat's settings. If the key may not list voices, a few built-in ones are
offered.

### Asking about a chart

A follow-up like "what does this reveal", "which make was highest in 2020",
or "when did Kia overtake Ford" is answered in words from the rows of the
chart on screen (or the one you name). Nothing is re-planned or redrawn, so
it takes a second or two. Questions that need data no chart has still make a
chart, and "why" questions get what the data shows plus something to chart
next, since the data cannot show causes.

### Sub-charts

Filtering a chart ("just Ford and Kia", "only since 2022", "the top five")
leaves it as it is and makes a sub-chart, listed under it and lettered: chart
1 has 1a, 1b, and so on, so "show me 1a and 1b side by side" works. Where the
filter can be answered from the rows the chart already has, the sub-chart
keeps those rows and nothing is totalled again; otherwise it gets a query of
its own, and its details say which. Changing a chart's query carries through
to the sub-charts that keep its rows, and marks the others as possibly out of
date. Sub-charts are one level deep, 26 to a chart; past that, talkviz offers
a new chart instead. "Change this one" still edits a chart in place.

A model you choose runs at its own default effort unless you set one.

## Use

Register a DuckDB database with the manifest that describes it, then ask:

```sh
uv run talkviz add path/to/data.duckdb path/to/manifest.yaml
uv run talkviz ask "How many orders were there each month?"
uv run talkviz ask          # no question: start a conversation
```

`talkviz serve` opens the web app at http://127.0.0.1:8321: chats on the left,
the chart in focus in the centre, the conversation below it, and the chat's
charts on the right. Chats, charts, and every result are kept in a local
SQLite file.

Charts are drawn with Plotly from a small spec the model fills in: headline
number, line, area, bars, stacked bars, donut, treemap, heatmap, scatter, 3D
scatter, and table. Each chart offers every other view its result fits, so
you can switch between, say, lines, stacked bars, and a heatmap without
asking again. The Download button saves the chart as a PNG or its data as CSV.

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
Each plan is timed, and the score is followed by the median and 90th
percentile plan time.

```sh
uv run talkviz eval path/to/eval.yaml --model anthropic/claude-haiku-5.5 --effort low
```

## Development

```sh
uv sync
uv run pre-commit install   # once per clone
uv run pytest
uv run ruff check . && uv run ruff format --check . && uv run ty check
```

The interface is a React app in `frontend/`, built into the Python package:

```sh
cd frontend && npm install
npm run build   # into src/talkviz/web, served by `talkviz serve`
npm run dev     # or: live reload, with `talkviz serve` running alongside
```

The hooks run ruff, ty, and a lockfile check on commit, and the tests on
push. CI runs the same checks on every pull request.

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org)
(`feat: ...`, `fix: ...`, `docs: ...`); a hook checks the format. Releases are
automated from them: on each push to `main`, release-please updates a release
pull request with the next version and changelog. `fix` bumps the patch
version, `feat` the minor, and `feat!` marks a breaking change. Merging that
pull request tags the release and publishes it to PyPI.
