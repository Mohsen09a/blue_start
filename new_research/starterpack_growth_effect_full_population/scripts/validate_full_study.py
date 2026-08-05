from __future__ import annotations

import json
from pathlib import Path

from blue_start.duckdb_backend import connect
from blue_start.settings import load_settings


ROOT = Path(__file__).resolve().parents[1]
SUMMARY = ROOT / "outputs" / "summaries" / "starterpack_growth_effect_full_population.json"
OUTPUT = ROOT / "outputs" / "summaries" / "starterpack_growth_effect_full_population_validation.json"


def main() -> int:
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    expected = int(summary["population_counts"]["matched_pairs"])
    with connect(load_settings(ROOT / "config.toml"), read_only=True) as con:
        checks = {
            "summary_complete": summary["complete"] is True,
            "all_eligible_treated_analyzed": summary["population_counts"]["eligible_and_analyzed_treated_users"] == 1084011,
            "cohort_count_matches": con.execute("SELECT count(*) = ? FROM results.starterpack_growth_matched_cohort", [expected]).fetchone()[0],
            "unique_treated_per_pair": con.execute("SELECT count(*) = count(DISTINCT treated_node_id) FROM results.starterpack_growth_matched_cohort").fetchone()[0],
            "no_self_matches": con.execute("SELECT count(*) = 0 FROM results.starterpack_growth_matched_cohort WHERE treated_node_id = control_node_id").fetchone()[0],
            "same_week_block": con.execute("""SELECT count(*) = 0 FROM results.starterpack_growth_matched_cohort WHERE floor(date_diff('day', DATE '1970-01-01', treated_index_date)/7.0) <> floor(date_diff('day', DATE '1970-01-01', control_index_date)/7.0)""").fetchone()[0],
            "controls_unexposed_90_days": con.execute("""SELECT count(*) = 0 FROM results.starterpack_growth_matched_cohort WHERE control_future_first_pack_date IS NOT NULL AND control_future_first_pack_date <= control_index_date + INTERVAL 90 DAY""").fetchone()[0],
            "outcomes_monotone": con.execute("""SELECT count(*) = 0 FROM results.starterpack_growth_matched_cohort WHERE treated_post_followers_7 > treated_post_followers_30 OR treated_post_followers_30 > treated_post_followers_90 OR control_post_followers_7 > control_post_followers_30 OR control_post_followers_30 > control_post_followers_90""").fetchone()[0],
            "reuse_cap_respected": con.execute("SELECT max(uses) <= 10 FROM (SELECT control_node_id, count(*) uses FROM results.starterpack_growth_matched_cohort GROUP BY control_node_id)").fetchone()[0],
            "balance_below_point_one": con.execute("SELECT max(abs(smd_after)) < 0.1 FROM results.starterpack_growth_balance").fetchone()[0],
            "effects_use_full_cohort": con.execute("SELECT count(*) = 0 FROM results.starterpack_growth_effects WHERE pairs <> ?", [expected]).fetchone()[0],
            "network_quality_complete": con.execute("SELECT count(*) = 2 AND min(new_followers_90) > 0 FROM results.starterpack_growth_network_quality").fetchone()[0],
            "all_256_quality_checkpoints": len(list((ROOT / 'work' / 'network_quality_batches').glob('bucket_*.json'))) == 256,
            "six_parquet_outputs": len(list((ROOT / 'outputs' / 'parquet').glob('*.parquet'))) == 6,
            "eight_figure_outputs": len(list((ROOT / 'outputs' / 'figures').glob('*.*'))) == 8,
        }
    checks["all_checks_passed"] = all(bool(value) for value in checks.values())
    OUTPUT.write_text(json.dumps({"matched_pairs": expected, "checks": checks}, indent=2), encoding="utf-8")
    print(json.dumps(checks, indent=2))
    return 0 if checks["all_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
