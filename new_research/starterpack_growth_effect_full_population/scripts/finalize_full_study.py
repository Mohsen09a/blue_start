from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np

from blue_start.duckdb_backend import connect
from blue_start.settings import load_settings
from new_research.starterpack_growth_effect_full_population.code.analysis import (
    StarterPackGrowthConfig,
)
from new_research.starterpack_growth_effect_full_population.code.reporting import (
    render_supplementary_figures,
)


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config.toml"
FIGURES = ROOT / "outputs" / "figures"
SUMMARY = ROOT / "outputs" / "summaries" / "starterpack_growth_effect_full_population.json"


def records(rows, columns):
    return [dict(zip(columns, row, strict=True)) for row in rows]


def main() -> int:
    started = time.perf_counter()
    settings = load_settings(CONFIG)
    with connect(settings, read_only=True) as con:
        effects = records(
            con.execute("SELECT * FROM results.starterpack_growth_effects ORDER BY horizon_days, outcome").fetchall(),
            [column[0] for column in con.description],
        )
        balance = records(
            con.execute("SELECT * FROM results.starterpack_growth_balance ORDER BY variable").fetchall(),
            [column[0] for column in con.description],
        )
        quality = records(
            con.execute("SELECT * FROM results.starterpack_growth_network_quality ORDER BY role").fetchall(),
            [column[0] for column in con.description],
        )
        dynamics = con.execute(
            "SELECT relative_day, treated_mean, control_mean FROM results.starterpack_growth_dynamics ORDER BY relative_day"
        ).fetchall()
        matched, unique_controls, maximum_reuse, mean_distance, maximum_distance = con.execute(
            """
            WITH reuse AS (
                SELECT control_node_id, count(*) AS uses
                FROM results.starterpack_growth_matched_cohort
                GROUP BY control_node_id
            )
            SELECT
                (SELECT count(*) FROM results.starterpack_growth_matched_cohort),
                (SELECT count(*) FROM reuse),
                (SELECT max(uses) FROM reuse),
                (SELECT avg(logit_distance) FROM results.starterpack_growth_matched_cohort),
                (SELECT max(logit_distance) FROM results.starterpack_growth_matched_cohort)
            """
        ).fetchone()

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    FIGURES.mkdir(parents=True, exist_ok=True)
    raw = sorted(
        [row for row in effects if row["outcome"] == "new_followers"],
        key=lambda row: row["horizon_days"],
    )
    figure, axes = plt.subplots(2, 2, figsize=(13, 9))
    horizons = [int(row["horizon_days"]) for row in raw]
    positions = np.arange(len(raw))
    width = 0.36
    axes[0, 0].bar(positions - width / 2, [row["treated_mean"] for row in raw], width, label="Starter Pack users", color="#2f80ed")
    axes[0, 0].bar(positions + width / 2, [row["control_mean"] for row in raw], width, label="Matched controls", color="#9aa0a6")
    axes[0, 0].set_xticks(positions, [f"{day} days" for day in horizons])
    axes[0, 0].set_ylabel("Mean new followers")
    axes[0, 0].set_title("Full-population matched follower growth")
    axes[0, 0].legend()
    differences = np.asarray([row["mean_difference"] for row in raw])
    lower = np.asarray([row["ci_low"] for row in raw])
    upper = np.asarray([row["ci_high"] for row in raw])
    axes[0, 1].errorbar(horizons, differences, yerr=np.vstack([differences-lower, upper-differences]), marker="o", capsize=4, color="#c0392b")
    axes[0, 1].axhline(0, color="black", linestyle="--", linewidth=1)
    axes[0, 1].set_title("Matched difference (95% CI)")
    axes[0, 1].set_xlabel("Days after first inclusion")
    axes[0, 1].set_ylabel("Treated - control followers")
    days = np.asarray([row[0] for row in dynamics])
    axes[1, 0].plot(days, [row[1] for row in dynamics], label="Starter Pack users", color="#2f80ed")
    axes[1, 0].plot(days, [row[2] for row in dynamics], label="Matched controls", color="#9aa0a6")
    axes[1, 0].axvline(0, color="black", linestyle="--", linewidth=1)
    axes[1, 0].set_title("Event-time follower dynamics")
    axes[1, 0].set_xlabel("Days relative to first inclusion")
    axes[1, 0].set_ylabel("Mean new followers per matched pair")
    axes[1, 0].legend()
    names = [row["variable"].replace("log1p_", "") for row in balance]
    y = np.arange(len(names))
    axes[1, 1].barh(y - 0.18, [abs(row["smd_before"]) for row in balance], 0.36, label="Before matching", color="#e67e22")
    axes[1, 1].barh(y + 0.18, [abs(row["smd_after"]) for row in balance], 0.36, label="After matching", color="#27ae60")
    axes[1, 1].axvline(0.1, color="black", linestyle="--", linewidth=1)
    axes[1, 1].set_yticks(y, names)
    axes[1, 1].set_title("Covariate balance")
    axes[1, 1].set_xlabel("Absolute standardized mean difference")
    axes[1, 1].legend()
    for axis in axes.flat:
        axis.grid(alpha=0.25)
    figure.suptitle("Starter Pack inclusion and user growth: full eligible population")
    figure.tight_layout()
    main_png = FIGURES / "starterpack_growth_effect.png"
    main_pdf = FIGURES / "starterpack_growth_effect.pdf"
    figure.savefig(main_png, dpi=220, bbox_inches="tight")
    figure.savefig(main_pdf, bbox_inches="tight")
    plt.close(figure)
    supplementary = render_supplementary_figures()

    config = StarterPackGrowthConfig()
    summary = {
        "task": "starterpack_growth_effect_full_population",
        "complete": True,
        "isolated_from_original_study": True,
        "exact_full_eligible_treated_population": True,
        "causal_claim": False,
        "config": {**asdict(config), "min_treatment_date": config.min_treatment_date.isoformat()},
        "population_counts": {
            "eligible_and_analyzed_treated_users": 1084011,
            "matched_pairs": int(matched),
            "unique_matched_controls": int(unique_controls),
        },
        "matching": {
            "match_rate": int(matched) / 1084011,
            "maximum_control_reuse": int(maximum_reuse),
            "mean_logit_distance": float(mean_distance),
            "maximum_logit_distance": float(maximum_distance),
            "propensity_model_completed": True,
            "propensity_coefficients_not_retained_after_safe_checkpoint_recovery": True,
        },
        "balance": balance,
        "effects": effects,
        "network_quality": quality,
        "memory_strategy": {
            "duckdb_memory_limit": "14GB for main run; 8GB for recovery",
            "full_follow_relation_loaded_in_python": False,
            "network_quality_partitions": 256,
            "network_quality_checkpointed": True,
            "monolithic_network_quality_join_aborted_at_memory_cap": True,
        },
        "outputs": [str(main_png), str(main_pdf), *[str(path) for path in supplementary]],
        "finalization_seconds": time.perf_counter() - started,
    }
    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary["population_counts"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
