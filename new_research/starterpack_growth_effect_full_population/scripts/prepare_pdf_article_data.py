from __future__ import annotations

import json
from pathlib import Path

import duckdb


ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = ROOT / "outputs"
WORK = ROOT / "work"
DESTINATION = ROOT / "tmp" / "pdfs" / "article_data.json"


def rows(connection: duckdb.DuckDBPyConnection, query: str) -> list[dict[str, object]]:
    result = connection.execute(query)
    columns = [column[0] for column in result.description]
    return [dict(zip(columns, row, strict=True)) for row in result.fetchall()]


def main() -> int:
    database = WORK / "full_population.duckdb"
    summary_path = OUTPUTS / "summaries" / "starterpack_growth_effect_full_population.json"
    validation_path = (
        OUTPUTS
        / "summaries"
        / "starterpack_growth_effect_full_population_validation.json"
    )
    with duckdb.connect(str(database), read_only=True) as connection:
        data = {
            "summary": json.loads(summary_path.read_text(encoding="utf-8")),
            "validation": json.loads(validation_path.read_text(encoding="utf-8")),
            "effects": rows(
                connection,
                "SELECT * FROM results.starterpack_growth_effects ORDER BY horizon_days, outcome",
            ),
            "balance": rows(
                connection,
                "SELECT * FROM results.starterpack_growth_balance ORDER BY variable",
            ),
            "subgroups_90": rows(
                connection,
                """
                SELECT dimension, subgroup, pairs, treated_mean, control_mean,
                       mean_difference, ci_low, ci_high
                FROM results.starterpack_growth_subgroups
                WHERE horizon_days = 90
                ORDER BY dimension, mean_difference DESC
                """,
            ),
            "network_quality": rows(
                connection,
                "SELECT * FROM results.starterpack_growth_network_quality ORDER BY role",
            ),
            "dynamics_summary": rows(
                connection,
                """
                SELECT
                    max(treated_mean) FILTER (WHERE relative_day > 0) AS peak_treated_daily,
                    arg_max(relative_day, treated_mean) FILTER (WHERE relative_day > 0) AS peak_treated_day,
                    max(mean_difference) FILTER (WHERE relative_day > 0) AS peak_daily_difference,
                    arg_max(relative_day, mean_difference) FILTER (WHERE relative_day > 0) AS peak_difference_day,
                    avg(mean_difference) FILTER (WHERE relative_day BETWEEN -90 AND -1) AS pre_mean_difference,
                    avg(mean_difference) FILTER (WHERE relative_day BETWEEN 1 AND 90) AS post_mean_difference
                FROM results.starterpack_growth_dynamics
                """,
            )[0],
            "cohort_summary": rows(
                connection,
                """
                WITH reuse AS (
                    SELECT control_node_id, count(*) AS uses
                    FROM results.starterpack_growth_matched_cohort
                    GROUP BY control_node_id
                )
                SELECT
                    count(*) AS matched_pairs,
                    count(DISTINCT treated_node_id) AS unique_treated,
                    count(DISTINCT control_node_id) AS unique_controls,
                    avg(logit_distance) AS mean_logit_distance,
                    max(logit_distance) AS maximum_logit_distance,
                    (SELECT max(uses) FROM reuse) AS maximum_control_reuse
                FROM results.starterpack_growth_matched_cohort
                """,
            )[0],
        }

    inventory = []
    for path in sorted(OUTPUTS.rglob("*")):
        if path.is_file():
            inventory.append(
                {
                    "path": path.relative_to(ROOT).as_posix(),
                    "bytes": path.stat().st_size,
                }
            )
    data["output_inventory"] = inventory
    data["network_checkpoint_count"] = len(
        list((WORK / "network_quality_batches").glob("bucket_*.json"))
    )
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    DESTINATION.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(DESTINATION)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
