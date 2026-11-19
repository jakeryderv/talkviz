import argparse
import sys
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from talkviz import llm
from talkviz.evaluate import evaluate, plan_seconds
from talkviz.llm import Exchange
from talkviz.sources import add_source, list_sources, load_source
from talkviz.turn import Turn, take_turn
from talkviz.workspace import Workspace

PREVIEW_ROWS = 12


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="talkviz", description="Talk to your data and see it charted."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    add = commands.add_parser("add", help="register a data source")
    add.add_argument("database", type=Path, help="DuckDB database file")
    add.add_argument("manifest", type=Path, help="YAML manifest describing it")

    commands.add_parser("sources", help="list registered data sources")

    ask = commands.add_parser("ask", help="ask one question, or chat if none is given")
    ask.add_argument("question", nargs="?")
    _common(ask)
    ask.add_argument(
        "--speech-model",
        help="OpenRouter model for the reply (default: TALKVIZ_SPEECH_MODEL, else"
        f" {llm.DEFAULT_SPEECH_MODEL or 'the planning model'})",
    )
    ask.add_argument(
        "--speech-effort",
        choices=llm.EFFORTS,
        help="reasoning effort for the reply model (default: TALKVIZ_SPEECH_EFFORT)",
    )

    evaluation = commands.add_parser("eval", help="score a model on an eval file")
    evaluation.add_argument("eval_file", type=Path)
    _common(evaluation)

    args = parser.parse_args()
    load_dotenv(find_dotenv(usecwd=True))
    try:
        _run(args)
    except (LookupError, FileNotFoundError, ValueError, llm.ModelFailed) as error:
        sys.exit(f"talkviz: {error}")


def _common(command: argparse.ArgumentParser) -> None:
    command.add_argument("--source", help="source name, if more than one is registered")
    command.add_argument(
        "--model", help=f"OpenRouter model (default {llm.DEFAULT_MODEL})"
    )
    command.add_argument(
        "--effort",
        choices=llm.EFFORTS,
        help="reasoning effort (default: TALKVIZ_EFFORT, else the model's own)",
    )


def _run(args: argparse.Namespace) -> None:
    if args.command == "add":
        source = add_source(args.database, args.manifest)
        print(f"added {source.name} ({source.database})")
    elif args.command == "sources":
        print("\n".join(list_sources()) or "no sources; add one with `talkviz add`")
    elif args.command == "ask":
        _ask(args)
    elif args.command == "eval":
        _eval(args)


def _eval(args: argparse.Namespace) -> None:
    source = load_source(args.source)
    model = llm.openrouter(args.model, args.effort)
    outcomes = evaluate(model, source, args.eval_file)
    for outcome in outcomes:
        retried = f" ({outcome.calls} calls)" if (outcome.calls or 1) > 1 else ""
        print(
            f"{'pass' if outcome.correct else 'FAIL'}  {outcome.seconds:5.1f}s"
            f"  {outcome.question}{retried}"
        )
        if not outcome.correct:
            print(f"      {outcome.detail}")
            if outcome.sql:
                print(f"      {' '.join(outcome.sql.split())}")
    correct = sum(outcome.correct for outcome in outcomes)
    median, p90 = plan_seconds(outcomes)
    print(
        f"\n{correct}/{len(outcomes)} correct ({_describe(model)});"
        f" plan median {median:.1f}s, p90 {p90:.1f}s"
    )


def _describe(model: llm.Model) -> str:
    effort = (model.settings or {}).get("openrouter_reasoning", {}).get("effort")
    return f"{model.model_name}, effort {effort}" if effort else model.model_name


def _ask(args: argparse.Namespace) -> None:
    source = load_source(args.source)
    model = llm.openrouter(args.model, args.effort)
    speaker = llm.speech_openrouter(args.speech_model, args.speech_effort)
    workspace, history = Workspace(), list[Exchange]()
    with source.connect() as con:
        print(f"{source.manifest.title}, data as of {source.data_version(con)}")
        questions = [args.question] if args.question else iter(_prompt, None)
        for question in questions:
            turn = take_turn(
                model, question, source.manifest, con, workspace, history, speaker
            )
            _show(turn)


def _prompt() -> str | None:
    try:
        return input("\n> ").strip() or None
    except EOFError:
        return None


def _show(turn: Turn) -> None:
    for run in turn.runs:
        chart, result = run.chart, run.result
        print(f"\n[{chart.id} r{chart.revision}] {chart.title}  ({run.op})")
        print(f"  spec: {chart.spec.model_dump_json(exclude_none=True)}")
        print(f"  sql:  {' '.join(chart.sql.split())}")
        print("  " + " | ".join(column.name for column in result.columns))
        for row in result.rows[:PREVIEW_ROWS]:
            print("  " + " | ".join(str(value) for value in row))
        if len(result.rows) > PREVIEW_ROWS:
            print(f"  … {len(result.rows)} rows")
    flag = "" if turn.reply_verified else " [unverified fallback]"
    print(f"\n{turn.reply}{flag}")
    print(f"  (plan {turn.seconds['plan']:.1f}s, speak {turn.seconds['speak']:.1f}s)")
