from __future__ import annotations

import json
from pathlib import Path

import duckdb


STUDY_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATABASE = PROJECT_ROOT / "work" / "blue_start.duckdb"
SUMMARY = STUDY_ROOT / "outputs" / "summaries" / "starterpack_growth_effect.json"
OUTPUT = STUDY_ROOT / "outputs" / "summaries" / "starterpack_growth_validation.json"


def scalar(connection: duckdb.DuckDBPyConnection, query: str) -> object:
    return connection.execute(query).fetchone()[0]


def main() -> int:
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))["summary"]
    expected_pairs = int(summary["population_counts"]["matched_pairs"])
    reuse_cap = int(summary["config"]["max_control_reuse"])
    with duckdb.connect(str(DATABASE), read_only=True) as con:
        checks = {
            "summary_complete": bool(summary["complete"]),
            "cohort_count_matches_summary": scalar(
                con, "SELECT count(*) FROM results.starterpack_growth_matched_cohort"
            )
            == expected_pairs,
            "unique_match_ids": scalar(
                con,
                """SELECT count(*) = count(DISTINCT match_id)
                   FROM results.starterpack_growth_matched_cohort""",
            ),
            "no_self_matches": scalar(
                con,
                """SELECT count(*) = 0
                   FROM results.starterpack_growth_matched_cohort
                   WHERE treated_node_id = control_node_id""",
            ),
            "same_seven_day_time_block": scalar(
                con,
                """SELECT count(*) = 0
                   FROM results.starterpack_growth_matched_cohort
                   WHERE floor(date_diff('day', DATE '1970-01-01', treated_index_date) / 7.0)
                      <> floor(date_diff('day', DATE '1970-01-01', control_index_date) / 7.0)
                      OR abs(date_diff('day', treated_index_date, control_index_date)) > 6""",
            ),
            "controls_unexposed_through_day_90": scalar(
                con,
                """SELECT count(*) = 0
                   FROM results.starterpack_growth_matched_cohort
                   WHERE control_future_first_pack_date IS NOT NULL
                     AND control_future_first_pack_date
                         <= control_index_date + INTERVAL 90 DAY""",
            ),
            "outcomes_are_monotone": scalar(
                con,
                """SELECT count(*) = 0
                   FROM results.starterpack_growth_matched_cohort
                   WHERE treated_post_followers_7 > treated_post_followers_30
                      OR treated_post_followers_30 > treated_post_followers_90
                      OR control_post_followers_7 > control_post_followers_30
                      OR control_post_followers_30 > control_post_followers_90""",
            ),
            "reuse_cap_respected": int(summary["matching"]["maximum_control_reuse"])
            <= reuse_cap,
            "no_day_zero_in_event_study": scalar(
                con,
                """SELECT count(*) = 0 FROM results.starterpack_growth_dynamics
                   WHERE relative_day = 0""",
            ),
            "post_match_balance_below_point_one": scalar(
                con,
                """SELECT coalesce(max(abs(smd_after)), 0) < 0.1
                   FROM results.starterpack_growth_balance""",
            ),
            "propensity_model_converged": bool(summary["propensity_model"]["converged"]),
            "cluster_robust_effect_intervals": scalar(
                con,
                """SELECT count(*) = 0 FROM results.starterpack_growth_effects
                   WHERE standard_error_method <> 'control-cluster-robust'""",
            ),
        }
        # Parameterized separately because the tiny helper intentionally handles scalars only.
        checks["all_effect_rows_use_full_matched_cohort"] = (
            con.execute(
                """SELECT count(*) = 0 FROM results.starterpack_growth_effects
                   WHERE pairs <> ?""",
                [expected_pairs],
            ).fetchone()[0]
        )
        subgroup_extremes = con.execute(
            """
            SELECT dimension, subgroup, pairs, mean_difference, ci_low, ci_high
            FROM results.starterpack_growth_subgroups
            WHERE horizon_days = 90
            ORDER BY mean_difference DESC
            """
        ).fetchall()

    checks["all_checks_passed"] = all(bool(value) for value in checks.values())
    result = {
        "matched_pairs": expected_pairs,
        "maximum_control_reuse": int(summary["matching"]["maximum_control_reuse"]),
        "maximum_absolute_post_match_smd": max(
            abs(float(row["smd_after"])) for row in summary["balance"]
        ),
        "checks": checks,
        "subgroup_effects_90_days": [
            {
                "dimension": row[0],
                "subgroup": row[1],
                "pairs": int(row[2]),
                "mean_difference": float(row[3]),
                "ci_low": float(row[4]),
                "ci_high": float(row[5]),
            }
            for row in subgroup_extremes
        ],
    }
    OUTPUT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if checks["all_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
