import yaml
from test_llm import Scripted, create

from talkviz.evaluate import evaluate, same_data
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
