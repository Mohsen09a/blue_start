from __future__ import annotations

from pathlib import Path

import numpy as np

from blue_start.duckdb_backend import connect
from blue_start.settings import load_settings


STUDY_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = STUDY_ROOT / "config.toml"
FIGURE_OUTPUTS = STUDY_ROOT / "outputs" / "figures"


def _save(figure, stem: str) -> list[Path]:
    FIGURE_OUTPUTS.mkdir(parents=True, exist_ok=True)
    png = FIGURE_OUTPUTS / f"{stem}.png"
    pdf = FIGURE_OUTPUTS / f"{stem}.pdf"
    figure.savefig(png, dpi=220, bbox_inches="tight")
    figure.savefig(pdf, bbox_inches="tight")
    return [png, pdf]


def render_supplementary_figures() -> list[Path]:
    """Render report-focused figures from the saved final result tables."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    settings = load_settings(CONFIG_PATH)
    with connect(settings, read_only=True) as con:
        effects = con.execute(
            """
            SELECT outcome, horizon_days, treated_mean, control_mean,
                   mean_difference, ci_low, ci_high
            FROM results.starterpack_growth_effects
            WHERE outcome IN (
                'new_followers', 'new_followers_winsorized_p99', 'any_new_follower'
            )
            ORDER BY horizon_days, outcome
            """
        ).fetchall()
        subgroups = con.execute(
            """
            SELECT dimension, subgroup, pairs, mean_difference, ci_low, ci_high
            FROM results.starterpack_growth_subgroups
            WHERE horizon_days = 90
            ORDER BY dimension, mean_difference
            """
        ).fetchall()
        quality = con.execute(
            """
            SELECT role, reciprocal_share, same_final_community_share
            FROM results.starterpack_growth_network_quality
            ORDER BY role DESC
            """
        ).fetchall()

    outputs: list[Path] = []
    blue = "#2f80ed"
    gray = "#9aa0a6"
    red = "#c0392b"
    green = "#27ae60"

    # Sensitivity and probability outcomes.
    effect_map = {(str(row[0]), int(row[1])): row for row in effects}
    figure, axes = plt.subplots(1, 2, figsize=(12.5, 4.8))
    labels = ["Raw mean", "99th-percentile capped"]
    rows = [effect_map[("new_followers", 90)], effect_map[("new_followers_winsorized_p99", 90)]]
    differences = np.asarray([float(row[4]) for row in rows])
    lower = np.asarray([float(row[5]) for row in rows])
    upper = np.asarray([float(row[6]) for row in rows])
    y = np.arange(len(labels))
    axes[0].errorbar(
        differences,
        y,
        xerr=np.vstack([differences - lower, upper - differences]),
        fmt="o",
        markersize=8,
        capsize=5,
        color=red,
    )
    axes[0].axvline(0, color="black", linewidth=1)
    axes[0].set_yticks(y, labels)
    axes[0].set_xlabel("Additional followers versus matched controls")
    axes[0].set_title("90-day result remains large after capping outliers")
    axes[0].grid(axis="x", alpha=0.25)
    for index, value in enumerate(differences):
        axes[0].annotate(f"+{value:.1f}", (value, index), xytext=(8, 0), textcoords="offset points", va="center")

    horizons = [7, 30, 90]
    treated_rates = [float(effect_map[("any_new_follower", day)][2]) * 100 for day in horizons]
    control_rates = [float(effect_map[("any_new_follower", day)][3]) * 100 for day in horizons]
    positions = np.arange(len(horizons))
    width = 0.36
    axes[1].bar(positions - width / 2, treated_rates, width, color=blue, label="Starter Pack users")
    axes[1].bar(positions + width / 2, control_rates, width, color=gray, label="Matched controls")
    axes[1].set_xticks(positions, [f"{day} days" for day in horizons])
    axes[1].set_ylabel("Users gaining at least one follower (%)")
    axes[1].set_ylim(0, 105)
    axes[1].set_title("The association is not only a celebrity-outlier effect")
    axes[1].legend(loc="lower right")
    axes[1].grid(axis="y", alpha=0.25)
    for position, treated_rate, control_rate in zip(positions, treated_rates, control_rates, strict=True):
        axes[1].text(position - width / 2, treated_rate + 1.5, f"{treated_rate:.1f}%", ha="center", fontsize=9)
        axes[1].text(position + width / 2, control_rate + 1.5, f"{control_rate:.1f}%", ha="center", fontsize=9)
    figure.suptitle("Robustness of the Starter Pack growth association")
    figure.tight_layout()
    outputs.extend(_save(figure, "starterpack_growth_robustness"))
    plt.close(figure)

    # Four subgroup dimensions on consistent horizontal effect plots.
    dimension_titles = {
        "account_age": "Account age",
        "baseline_followers": "Baseline follower count",
        "first_pack_size": "Size of first pack",
        "packs_within_30_days": "Pack exposures within 30 days",
    }
    dimension_order = list(dimension_titles)
    figure, axes = plt.subplots(2, 2, figsize=(13, 9))
    for axis, dimension in zip(axes.flat, dimension_order, strict=True):
        rows = [row for row in subgroups if row[0] == dimension]
        labels = [str(row[1]) for row in rows]
        values = np.asarray([float(row[3]) for row in rows])
        lows = np.asarray([float(row[4]) for row in rows])
        highs = np.asarray([float(row[5]) for row in rows])
        positions = np.arange(len(rows))
        axis.errorbar(
            values,
            positions,
            xerr=np.vstack([values - lows, highs - values]),
            fmt="o",
            color=blue,
            capsize=4,
        )
        axis.axvline(0, color="black", linewidth=1)
        axis.set_yticks(positions, labels)
        axis.set_xlabel("Additional 90-day followers")
        axis.set_title(dimension_titles[dimension])
        axis.grid(axis="x", alpha=0.25)
        for position, value in zip(positions, values, strict=True):
            axis.annotate(f"+{value:.0f}", (value, position), xytext=(7, 0), textcoords="offset points", va="center", fontsize=9)
    figure.suptitle("Where the Starter Pack growth association is strongest")
    figure.tight_layout()
    outputs.extend(_save(figure, "starterpack_growth_subgroups"))
    plt.close(figure)

    # Relationship-quality comparison.
    roles = ["Starter Pack users" if row[0] == "treated" else "Matched controls" for row in quality]
    reciprocal = [float(row[1]) * 100 for row in quality]
    community = [float(row[2]) * 100 for row in quality]
    figure, axes = plt.subplots(1, 2, figsize=(10.5, 4.5))
    colors = [blue if role == "Starter Pack users" else gray for role in roles]
    axes[0].bar(roles, reciprocal, color=colors)
    axes[0].set_ylabel("New follower edges that were reciprocal (%)")
    axes[0].set_title("Reciprocity by day 90")
    axes[0].set_ylim(0, 40)
    axes[0].grid(axis="y", alpha=0.25)
    axes[1].bar(roles, community, color=colors)
    axes[1].set_ylabel("Known-label pairs in the same final community (%)")
    axes[1].set_title("Descriptive community overlap")
    axes[1].set_ylim(0, 80)
    axes[1].grid(axis="y", alpha=0.25)
    for axis, values in zip(axes, [reciprocal, community], strict=True):
        for index, value in enumerate(values):
            axis.text(index, value + 1, f"{value:.1f}%", ha="center")
    figure.suptitle("Quality and composition of newly gained follower edges")
    figure.tight_layout()
    outputs.extend(_save(figure, "starterpack_growth_network_quality"))
    plt.close(figure)

    return outputs
