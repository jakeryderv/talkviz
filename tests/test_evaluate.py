import yaml
from test_llm import Scripted, create

from talkviz.evaluate import Outcome, evaluate, plan_seconds, same_data
from talkviz.sources import Source
from talkviz.sql import run

TOTALS = "SELECT category, sum(amount) AS total FROM orders GROUP BY category"


def test_same_data_ignores_names_order_and_extra_columns(con):
    expected = run(con, TOTALS)
    renamed = "SELECT sum(amount) AS sales, count(*) AS n, category AS kind FROM orders GROUP BY category ORDER BY sales"
    assert same_data(expected, run(con, renamed))


def test_same_data_spots_different_values_and_row_counts(con):
    expected = run(con, TOTALS)
    assert not same_data(expected, run(con, TOTALS + " HAVING sum(amount) > 36"))
    counts = "SELECT category, count(*) AS total FROM orders GROUP BY category"
    assert not same_data(expected, run(con, counts))
    assert not same_data(expected, run(con, "SELECT category FROM orders GROUP BY 1"))


def test_evaluate_scores_each_question(database, manifest, tmp_path):
    eval_file = tmp_path / "eval.yaml"
    eval_file.write_text(
        yaml.safe_dump(
            [
                {"question": "sales by category?", "sql": TOTALS},
                {"question": "how many orders?", "sql": "SELECT count(*) FROM orders"},
                {"question": "what's the weather?", "sql": "SELECT 1"},
            ]
        )
    )
    wrong = create(
        title="Orders",
        sql="SELECT sum(amount) AS n FROM orders",
        spec={"kind": "table"},
    )
    model = Scripted(
        {"actions": [create()]},
        {"actions": [wrong]},
        {"actions": [], "note": "No weather data."},
    )
    outcomes = evaluate(model, Source("shop", database, manifest), eval_file)
    assert [o.correct for o in outcomes] == [True, False, False]
    assert outcomes[1].sql == "SELECT sum(amount) AS n FROM orders"
    assert "No weather data." in outcomes[2].detail
    assert all(o.seconds > 0 for o in outcomes)
    assert [o.calls for o in outcomes] == [1, 1, 1]


def test_evaluate_counts_calls_when_a_plan_is_sent_back(database, manifest, tmp_path):
    eval_file = tmp_path / "eval.yaml"
    eval_file.write_text(yaml.safe_dump([{"question": "sales?", "sql": TOTALS}]))
    bad = create(sql="SELECT * FROM secrets")
    model = Scripted({"actions": [bad]}, {"actions": [create()]})
    [outcome] = evaluate(model, Source("shop", database, manifest), eval_file)
    assert (outcome.correct, outcome.calls) == (True, 2)


def test_plan_seconds_gives_median_and_90th_percentile():
    outcomes = [Outcome("q", True, None, "", float(s), 1) for s in range(1, 11)]
    assert plan_seconds(outcomes) == (5.5, 9.0)
    assert plan_seconds(outcomes[:1]) == (1.0, 1.0)
    assert plan_seconds([]) == (0.0, 0.0)
