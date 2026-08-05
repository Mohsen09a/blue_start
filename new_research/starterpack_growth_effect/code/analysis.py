from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit

from blue_start.duckdb_backend import connect, export_query
from blue_start.pipeline import RunResult
from blue_start.settings import load_settings

from .reporting import render_supplementary_figures


STUDY_ROOT = Path(__file__).resolve().parents[1]
PARQUET_OUTPUTS = STUDY_ROOT / "outputs" / "parquet"
SUMMARY_OUTPUTS = STUDY_ROOT / "outputs" / "summaries"
FIGURE_OUTPUTS = STUDY_ROOT / "outputs" / "figures"


VALID_EVENT_START = date(2022, 11, 17)


@dataclass(frozen=True)
class StarterPackGrowthConfig:
    treatment_rows: int = 100_000
    controls_per_treatment: int = 8
    min_account_age_days: int = 30
    min_treatment_date: date = date(2024, 6, 1)
    horizon_days: int = 90
    max_final_in_degree: int = 100_000
    max_final_out_degree: int = 100_000
    propensity_caliper: float = 0.25
    max_control_reuse: int = 10
    seed: int = 15


def age_band(age_days: float) -> str:
    if age_days < 91:
        return "30-90 days"
    if age_days < 366:
        return "91-365 days"
    if age_days < 731:
        return "1-2 years"
    return "2+ years"


def degree_band(degree: float) -> str:
    if degree < 10:
        return "0-9"
    if degree < 100:
        return "10-99"
    if degree < 1_000:
        return "100-999"
    return "1,000+"


def pack_size_band(size: float) -> str:
    if size <= 50:
        return "small (<=50)"
    if size <= 150:
        return "medium (51-150)"
    return "large (>150)"


def standardized_mean_difference(
    treated: np.ndarray, control: np.ndarray
) -> float:
    treated = np.asarray(treated, dtype=np.float64)
    control = np.asarray(control, dtype=np.float64)
    if len(treated) == 0 or len(control) == 0:
        return math.nan
    pooled = math.sqrt((float(treated.var()) + float(control.var())) / 2.0)
    if pooled == 0.0:
        return 0.0
    return (float(treated.mean()) - float(control.mean())) / pooled


def fit_propensity_model(
    features: np.ndarray,
    treated: np.ndarray,
    *,
    ridge: float = 1e-4,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Fit a small ridge-logistic propensity model without a large ML dependency."""
    x = np.asarray(features, dtype=np.float64)
    y = np.asarray(treated, dtype=np.float64)
    if x.ndim != 2 or y.ndim != 1 or len(x) != len(y):
        raise ValueError("invalid propensity-model array shapes")
    means = x.mean(axis=0)
    scales = x.std(axis=0)
    scales[scales <= 0.0] = 1.0
    z = (x - means) / scales
    design = np.column_stack([np.ones(len(z)), z])

    def objective(beta: np.ndarray) -> tuple[float, np.ndarray]:
        linear = design @ beta
        probabilities = expit(linear)
        value = float(np.logaddexp(0.0, linear).sum() - y @ linear)
        value += 0.5 * ridge * float(beta[1:] @ beta[1:])
        gradient = design.T @ (probabilities - y)
        gradient[1:] += ridge * beta[1:]
        return value, gradient

    result = minimize(
        lambda beta: objective(beta)[0],
        np.zeros(design.shape[1], dtype=np.float64),
        jac=lambda beta: objective(beta)[1],
        method="L-BFGS-B",
        options={"maxiter": 300, "ftol": 1e-10},
    )
    scores = np.clip(expit(design @ result.x), 1e-8, 1.0 - 1e-8)
    return scores, {
        "converged": bool(result.success),
        "iterations": int(result.nit),
        "negative_log_likelihood": float(objective(result.x)[0]),
        "coefficients": [float(value) for value in result.x],
        "feature_means": [float(value) for value in means],
        "feature_standard_deviations": [float(value) for value in scales],
    }


def nearest_propensity_matches(
    scores: np.ndarray,
    treated: np.ndarray,
    strata: np.ndarray,
    *,
    caliper_standard_deviations: float = 0.25,
    maximum_reuse: int | None = 10,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]:
    """Nearest-neighbour matching with replacement inside exact strata."""
    scores = np.asarray(scores, dtype=np.float64)
    treated = np.asarray(treated, dtype=bool)
    strata = np.asarray(strata)
    if len(scores) != len(treated) or len(scores) != len(strata):
        raise ValueError("matching arrays must have equal lengths")
    logits = np.log(scores / (1.0 - scores))
    caliper = caliper_standard_deviations * float(logits.std())
    if maximum_reuse is not None and maximum_reuse < 1:
        raise ValueError("maximum_reuse must be positive or None")
    reuse = np.zeros(len(scores), dtype=np.int32)
    control_groups: dict[Any, tuple[np.ndarray, np.ndarray]] = {}
    for key in np.unique(strata[~treated]):
        indexes = np.flatnonzero((~treated) & (strata == key))
        order = np.argsort(logits[indexes], kind="stable")
        control_groups[key] = (logits[indexes][order], indexes[order])

    treated_matches: list[int] = []
    control_matches: list[int] = []
    distances: list[float] = []
    for treated_index in np.flatnonzero(treated):
        group = control_groups.get(strata[treated_index])
        if group is None or len(group[0]) == 0:
            continue
        values, indexes = group
        right = int(np.searchsorted(values, logits[treated_index]))
        left = right - 1
        best: int | None = None
        distance = math.inf
        while left >= 0 or right < len(values):
            left_distance = (
                abs(float(values[left] - logits[treated_index])) if left >= 0 else math.inf
            )
            right_distance = (
                abs(float(values[right] - logits[treated_index]))
                if right < len(values)
                else math.inf
            )
            if min(left_distance, right_distance) > caliper:
                break
            if left_distance <= right_distance:
                candidate = left
                left -= 1
            else:
                candidate = right
                right += 1
            control_index = int(indexes[candidate])
            if maximum_reuse is None or reuse[control_index] < maximum_reuse:
                best = candidate
                distance = abs(float(values[best] - logits[treated_index]))
                break
        if best is not None:
            control_index = int(indexes[best])
            reuse[control_index] += 1
            treated_matches.append(int(treated_index))
            control_matches.append(control_index)
            distances.append(distance)

    control_array = np.asarray(control_matches, dtype=np.int64)
    unique_controls, reuse_counts = (
        np.unique(control_array, return_counts=True)
        if len(control_array)
        else (np.array([], dtype=np.int64), np.array([], dtype=np.int64))
    )
    diagnostics = {
        "caliper_logit_distance": caliper,
        "eligible_treated": int(treated.sum()),
        "matched_treated": len(treated_matches),
        "match_rate": len(treated_matches) / int(treated.sum()) if treated.any() else 0.0,
        "unique_controls": int(len(unique_controls)),
        "maximum_control_reuse": int(reuse_counts.max()) if len(reuse_counts) else 0,
        "mean_logit_distance": float(np.mean(distances)) if distances else math.nan,
        "maximum_logit_distance": float(np.max(distances)) if distances else math.nan,
    }
    return (
        np.asarray(treated_matches, dtype=np.int64),
        control_array,
        np.asarray(distances, dtype=np.float64),
        diagnostics,
    )


def paired_effect(
    treated: np.ndarray,
    control: np.ndarray,
    clusters: np.ndarray | None = None,
) -> dict[str, float | int | str]:
    treated = np.asarray(treated, dtype=np.float64)
    control = np.asarray(control, dtype=np.float64)
    if len(treated) == 0 or len(treated) != len(control):
        raise ValueError("paired outcome arrays must be non-empty and equally sized")
    differences = treated - control
    difference = float(differences.mean())
    if clusters is None:
        standard_error = float(differences.std(ddof=1) / math.sqrt(len(differences)))
        standard_error_method = "paired"
    else:
        cluster_values = np.asarray(clusters)
        if len(cluster_values) != len(differences):
            raise ValueError("clusters must have one value per pair")
        _, inverse = np.unique(cluster_values, return_inverse=True)
        cluster_sums = np.bincount(inverse, weights=differences - difference)
        cluster_count = len(cluster_sums)
        correction = cluster_count / (cluster_count - 1) if cluster_count > 1 else 1.0
        standard_error = math.sqrt(
            correction * float(cluster_sums @ cluster_sums) / (len(differences) ** 2)
        )
        standard_error_method = "control-cluster-robust"
    control_mean = float(control.mean())
    return {
        "pairs": int(len(treated)),
        "treated_mean": float(treated.mean()),
        "control_mean": control_mean,
        "mean_difference": difference,
        "ci_low": difference - 1.959963984540054 * standard_error,
        "ci_high": difference + 1.959963984540054 * standard_error,
        "standard_error": standard_error,
        "mean_ratio": float(treated.mean() / control_mean) if control_mean > 0 else math.nan,
        "treated_median": float(np.median(treated)),
        "control_median": float(np.median(control)),
        "standard_error_method": standard_error_method,
    }


def _stage(stages: dict[str, float], name: str, started: float) -> None:
    elapsed = time.perf_counter() - started
    stages[name] = elapsed
    print(f"[OK] {name}: {elapsed:.2f} seconds", flush=True)


def _write_summary(result: RunResult) -> RunResult:
    SUMMARY_OUTPUTS.mkdir(parents=True, exist_ok=True)
    path = SUMMARY_OUTPUTS / f"{result.task}.json"
    path.write_text(
        json.dumps(
            {
                "task": result.task,
                "seconds": result.seconds,
                "outputs": result.outputs,
                "summary": result.summary,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return RunResult(
        task=result.task,
        seconds=result.seconds,
        outputs=[*result.outputs, str(path)],
        summary=result.summary,
    )


def _insert_match_rows(
    con: Any,
    rows: list[tuple[int, int, int, float, float, float]],
) -> None:
    con.execute("DROP TABLE IF EXISTS growth_matches")
    con.execute(
        """
        CREATE TEMP TABLE growth_matches (
            match_id UINTEGER,
            treated_obs_id UBIGINT,
            control_obs_id UBIGINT,
            treated_propensity DOUBLE,
            control_propensity DOUBLE,
            logit_distance DOUBLE
        )
        """
    )
    for start in range(0, len(rows), 20_000):
        con.executemany(
            "INSERT INTO growth_matches VALUES (?, ?, ?, ?, ?, ?)",
            rows[start : start + 20_000],
        )


def run_starterpack_growth_study(
    config: StarterPackGrowthConfig | None = None,
) -> RunResult:
    """Estimate follower growth after first Starter Pack exposure using matched controls."""
    config = config or StarterPackGrowthConfig()
    if config.treatment_rows < 100:
        raise ValueError("treatment_rows must be at least 100")
    if config.controls_per_treatment < 1:
        raise ValueError("controls_per_treatment must be positive")
    if config.horizon_days < 90:
        raise ValueError("horizon_days must be at least 90")
    if config.min_account_age_days < 1:
        raise ValueError("min_account_age_days must be positive")
    if config.propensity_caliper <= 0:
        raise ValueError("propensity_caliper must be positive")
    if config.max_control_reuse < 1:
        raise ValueError("max_control_reuse must be positive")

    settings = load_settings()
    for output_directory in (PARQUET_OUTPUTS, SUMMARY_OUTPUTS, FIGURE_OUTPUTS):
        output_directory.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    stages: dict[str, float] = {}
    outputs: list[str] = []
    cohort_output = PARQUET_OUTPUTS / "starterpack_growth_matched_cohort.parquet"
    effects_output = PARQUET_OUTPUTS / "starterpack_growth_effects.parquet"
    balance_output = PARQUET_OUTPUTS / "starterpack_growth_balance.parquet"
    dynamics_output = PARQUET_OUTPUTS / "starterpack_growth_dynamics.parquet"
    subgroups_output = PARQUET_OUTPUTS / "starterpack_growth_subgroups.parquet"
    quality_output = PARQUET_OUTPUTS / "starterpack_growth_network_quality.parquet"

    with connect(settings) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS results")
        # Keep this study from consuming the entire remaining drive if a query spills.
        con.execute("SET max_temp_directory_size = '70GB'")
        required = {
            row[0]
            for row in con.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_name IN (
                    'indexed_follows_by_src', 'indexed_follows_by_dst',
                    'follow_degrees_full', 'starterpack_leiden_labels_local'
                )
                """
            ).fetchall()
        }
        missing = {"indexed_follows_by_src", "indexed_follows_by_dst", "follow_degrees_full"} - required
        if missing:
            raise RuntimeError(
                "Required prepared/indexed relations are missing: " + ", ".join(sorted(missing))
            )
        community_available = "starterpack_leiden_labels_local" in required

        stage_start = time.perf_counter()
        con.execute(
            """
            CREATE OR REPLACE TEMP TABLE growth_membership_unique AS
            SELECT
                m.pack_id,
                m.member_id::UINTEGER AS node_id,
                greatest(min(m.date_added), min(p.date_created))::DATE AS exposure_date,
                max(p.member_count)::UINTEGER AS pack_size,
                bool_or(p.creator_id = m.member_id) AS self_curated
            FROM starterpack_memberships AS m
            JOIN starterpacks AS p USING (pack_id)
            GROUP BY m.pack_id, m.member_id
            """
        )
        con.execute(
            """
            CREATE OR REPLACE TEMP TABLE growth_first_treatment AS
            SELECT
                node_id,
                min(exposure_date)::DATE AS first_pack_date,
                count(DISTINCT pack_id)::UINTEGER AS total_pack_count
            FROM growth_membership_unique
            GROUP BY node_id
            """
        )
        data_end = con.execute(
            f"SELECT max(date_followed) FROM indexed_follows_by_dst WHERE date_followed >= DATE '{VALID_EVENT_START}'"
        ).fetchone()[0]
        last_treatment_date = con.execute(
            f"SELECT DATE '{data_end}' - INTERVAL {config.horizon_days} DAY"
        ).fetchone()[0]
        con.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE growth_treatment_eligible AS
            SELECT
                ft.node_id,
                ft.first_pack_date AS index_date,
                ft.total_pack_count,
                n.date_created,
                n.active,
                d.in_degree AS final_in_degree,
                d.out_degree AS final_out_degree
            FROM growth_first_treatment AS ft
            JOIN nodes AS n USING (node_id)
            JOIN results.follow_degrees_full AS d USING (node_id)
            WHERE ft.first_pack_date >= DATE '{config.min_treatment_date}'
              AND ft.first_pack_date <= DATE '{last_treatment_date}'
              AND n.date_created <= ft.first_pack_date - INTERVAL {config.min_account_age_days} DAY
              AND d.in_degree <= {config.max_final_in_degree}
              AND d.out_degree <= {config.max_final_out_degree}
            """
        )
        eligible_treatments = int(
            con.execute("SELECT count(*) FROM growth_treatment_eligible").fetchone()[0]
        )
        con.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE growth_treatment_sample AS
            SELECT
                row_number() OVER (ORDER BY hash(node_id, {config.seed}), node_id)::UINTEGER AS treatment_id,
                *
            FROM (
                SELECT *
                FROM growth_treatment_eligible
                ORDER BY hash(node_id, {config.seed}), node_id
                LIMIT {config.treatment_rows}
            )
            """
        )
        treatment_count = int(
            con.execute("SELECT count(*) FROM growth_treatment_sample").fetchone()[0]
        )
        if treatment_count < 100:
            raise RuntimeError("too few eligible treated users")
        _stage(stages, "treatment_dates_and_sample", stage_start)

        stage_start = time.perf_counter()
        candidate_limit = treatment_count * config.controls_per_treatment
        con.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE growth_control_base AS
            SELECT
                row_number() OVER (ORDER BY hash(n.node_id, {config.seed + 101}), n.node_id)::UBIGINT AS candidate_rn,
                n.node_id,
                n.date_created,
                n.active,
                d.in_degree AS final_in_degree,
                d.out_degree AS final_out_degree
            FROM nodes AS n
            JOIN results.follow_degrees_full AS d USING (node_id)
            LEFT JOIN growth_treatment_sample AS sampled USING (node_id)
            WHERE sampled.node_id IS NULL
              AND d.in_degree <= {config.max_final_in_degree}
              AND d.out_degree <= {config.max_final_out_degree}
              AND hash(n.node_id, {config.seed + 101}) % 1000 < 40
            ORDER BY hash(n.node_id, {config.seed + 101}), n.node_id
            LIMIT {candidate_limit}
            """
        )
        con.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE growth_control_sample AS
            SELECT
                c.candidate_rn,
                c.node_id,
                t.index_date,
                c.date_created,
                c.active,
                c.final_in_degree,
                c.final_out_degree,
                ft.first_pack_date AS future_first_pack_date
            FROM growth_control_base AS c
            JOIN growth_treatment_sample AS t
              ON t.treatment_id = 1 + ((c.candidate_rn - 1) % {treatment_count})
            LEFT JOIN growth_first_treatment AS ft ON ft.node_id = c.node_id
            WHERE c.date_created <= t.index_date - INTERVAL {config.min_account_age_days} DAY
              AND (
                    ft.first_pack_date IS NULL
                    OR ft.first_pack_date > t.index_date + INTERVAL {config.horizon_days} DAY
              )
            """
        )
        control_count = int(con.execute("SELECT count(*) FROM growth_control_sample").fetchone()[0])
        if control_count < treatment_count:
            raise RuntimeError(
                f"too few eligible controls ({control_count}) for {treatment_count} treatments"
            )
        con.execute(
            """
            CREATE OR REPLACE TEMP TABLE growth_observations AS
            SELECT
                treatment_id::UBIGINT AS obs_id,
                true AS treated,
                node_id,
                index_date,
                date_created,
                active,
                total_pack_count,
                NULL::DATE AS future_first_pack_date
            FROM growth_treatment_sample
            UNION ALL
            SELECT
                (1000000000::UBIGINT + candidate_rn)::UBIGINT AS obs_id,
                false AS treated,
                node_id,
                index_date,
                date_created,
                active,
                NULL::UINTEGER AS total_pack_count,
                future_first_pack_date
            FROM growth_control_sample
            """
        )
        _stage(stages, "risk_set_controls", stage_start)

        stage_start = time.perf_counter()
        con.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE growth_in_features AS
            SELECT
                o.obs_id,
                count(f.src) FILTER (
                    WHERE f.date_followed >= DATE '{VALID_EVENT_START}'
                      AND f.date_followed < o.index_date
                )::UINTEGER AS pre_in_degree,
                count(f.src) FILTER (
                    WHERE f.date_followed >= o.index_date - INTERVAL 90 DAY
                      AND f.date_followed < o.index_date
                )::UINTEGER AS pre_followers_90,
                count(f.src) FILTER (
                    WHERE f.date_followed >= o.index_date - INTERVAL 30 DAY
                      AND f.date_followed < o.index_date
                )::UINTEGER AS pre_followers_30,
                count(f.src) FILTER (
                    WHERE f.date_followed >= o.index_date - INTERVAL 7 DAY
                      AND f.date_followed < o.index_date
                )::UINTEGER AS pre_followers_7,
                count(f.src) FILTER (
                    WHERE f.date_followed > o.index_date
                      AND f.date_followed <= o.index_date + INTERVAL 7 DAY
                )::UINTEGER AS post_followers_7,
                count(f.src) FILTER (
                    WHERE f.date_followed > o.index_date
                      AND f.date_followed <= o.index_date + INTERVAL 30 DAY
                )::UINTEGER AS post_followers_30,
                count(f.src) FILTER (
                    WHERE f.date_followed > o.index_date
                      AND f.date_followed <= o.index_date + INTERVAL 90 DAY
                )::UINTEGER AS post_followers_90
            FROM growth_observations AS o
            LEFT JOIN indexed_follows_by_dst AS f
              ON f.dst = o.node_id
             AND f.date_followed <= o.index_date + INTERVAL 90 DAY
            GROUP BY o.obs_id
            """
        )
        _stage(stages, "incoming_follow_features", stage_start)

        stage_start = time.perf_counter()
        con.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE growth_out_features AS
            SELECT
                o.obs_id,
                count(f.dst) FILTER (
                    WHERE f.date_followed >= DATE '{VALID_EVENT_START}'
                      AND f.date_followed < o.index_date
                )::UINTEGER AS pre_out_degree,
                count(f.dst) FILTER (
                    WHERE f.date_followed >= o.index_date - INTERVAL 30 DAY
                      AND f.date_followed < o.index_date
                )::UINTEGER AS pre_following_30,
                count(f.dst) FILTER (
                    WHERE f.date_followed > o.index_date
                      AND f.date_followed <= o.index_date + INTERVAL 7 DAY
                )::UINTEGER AS post_following_7,
                count(f.dst) FILTER (
                    WHERE f.date_followed > o.index_date
                      AND f.date_followed <= o.index_date + INTERVAL 30 DAY
                )::UINTEGER AS post_following_30,
                count(f.dst) FILTER (
                    WHERE f.date_followed > o.index_date
                      AND f.date_followed <= o.index_date + INTERVAL 90 DAY
                )::UINTEGER AS post_following_90
            FROM growth_observations AS o
            LEFT JOIN indexed_follows_by_src AS f
              ON f.src = o.node_id
             AND f.date_followed <= o.index_date + INTERVAL 90 DAY
            GROUP BY o.obs_id
            """
        )
        _stage(stages, "outgoing_follow_features", stage_start)

        stage_start = time.perf_counter()
        con.execute(
            """
            CREATE OR REPLACE TEMP TABLE growth_treatment_attributes AS
            SELECT
                t.treatment_id::UBIGINT AS obs_id,
                count(DISTINCT u.pack_id) FILTER (
                    WHERE u.exposure_date = t.index_date
                )::UINTEGER AS packs_on_first_day,
                count(DISTINCT u.pack_id) FILTER (
                    WHERE u.exposure_date <= t.index_date + INTERVAL 30 DAY
                )::UINTEGER AS packs_within_30_days,
                avg(u.pack_size) FILTER (
                    WHERE u.exposure_date = t.index_date
                )::DOUBLE AS mean_first_pack_size,
                max(u.pack_size) FILTER (
                    WHERE u.exposure_date = t.index_date
                )::UINTEGER AS largest_first_pack_size,
                bool_or(u.self_curated) FILTER (
                    WHERE u.exposure_date = t.index_date
                ) AS self_curated_first_day
            FROM growth_treatment_sample AS t
            JOIN growth_membership_unique AS u USING (node_id)
            GROUP BY t.treatment_id
            """
        )
        community_join = (
            "LEFT JOIN results.starterpack_leiden_labels_local AS l ON l.node_id = o.node_id"
            if community_available
            else ""
        )
        community_columns = (
            "l.community::INTEGER AS final_community, true AS final_community_available"
            if community_available
            else "NULL::INTEGER AS final_community, false AS final_community_available"
        )
        con.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE growth_features AS
            SELECT
                o.*,
                date_diff('day', o.date_created, o.index_date)::INTEGER AS account_age_days,
                i.pre_in_degree,
                x.pre_out_degree,
                i.pre_followers_7,
                i.pre_followers_30,
                i.pre_followers_90,
                x.pre_following_30,
                i.post_followers_7,
                i.post_followers_30,
                i.post_followers_90,
                x.post_following_7,
                x.post_following_30,
                x.post_following_90,
                a.packs_on_first_day,
                a.packs_within_30_days,
                a.mean_first_pack_size,
                a.largest_first_pack_size,
                a.self_curated_first_day,
                {community_columns}
            FROM growth_observations AS o
            JOIN growth_in_features AS i USING (obs_id)
            JOIN growth_out_features AS x USING (obs_id)
            LEFT JOIN growth_treatment_attributes AS a USING (obs_id)
            {community_join}
            """
        )
        arrays = con.execute(
            """
            SELECT
                obs_id, treated, node_id, index_date, account_age_days,
                pre_in_degree, pre_out_degree, pre_followers_30, pre_followers_90,
                pre_following_30, post_followers_7, post_followers_30,
                post_followers_90, post_following_7, post_following_30,
                post_following_90, coalesce(packs_on_first_day, 0) AS packs_on_first_day,
                coalesce(packs_within_30_days, 0) AS packs_within_30_days,
                coalesce(mean_first_pack_size, 0) AS mean_first_pack_size,
                coalesce(largest_first_pack_size, 0) AS largest_first_pack_size
            FROM growth_features
            ORDER BY obs_id
            """
        ).fetchnumpy()
        _stage(stages, "analysis_features", stage_start)

        stage_start = time.perf_counter()
        feature_names = [
            "log1p_pre_in_degree",
            "log1p_pre_out_degree",
            "log1p_pre_followers_30",
            "log1p_pre_following_30",
            "log1p_account_age_days",
        ]
        feature_matrix = np.column_stack(
            [
                np.log1p(arrays["pre_in_degree"].astype(np.float64)),
                np.log1p(arrays["pre_out_degree"].astype(np.float64)),
                np.log1p(arrays["pre_followers_30"].astype(np.float64)),
                np.log1p(arrays["pre_following_30"].astype(np.float64)),
                np.log1p(arrays["account_age_days"].astype(np.float64)),
            ]
        )
        treated_flags = arrays["treated"].astype(bool)
        propensity, propensity_diagnostics = fit_propensity_model(
            feature_matrix, treated_flags
        )
        # Exact fixed seven-day and coarse account-age matching controls platform time
        # shocks and prevents very new accounts from matching old accounts.
        date_days = arrays["index_date"].astype("datetime64[D]").astype(np.int64)
        weeks = np.floor_divide(date_days, 7)
        ages = arrays["account_age_days"].astype(np.int64)
        age_bins = np.select(
            [ages <= 90, ages <= 365, ages <= 730], [0, 1, 2], default=3
        ).astype(np.int64)
        strata = weeks * 10 + age_bins
        treated_indexes, control_indexes, distances, matching_diagnostics = (
            nearest_propensity_matches(
                propensity,
                treated_flags,
                strata,
                caliper_standard_deviations=config.propensity_caliper,
                maximum_reuse=config.max_control_reuse,
            )
        )
        minimum_matches = max(100, min(1_000, treatment_count // 5))
        if len(treated_indexes) < minimum_matches:
            raise RuntimeError(
                f"only {len(treated_indexes)} treated users could be matched "
                f"(minimum required: {minimum_matches})"
            )
        match_rows = [
            (
                match_id,
                int(arrays["obs_id"][treated_index]),
                int(arrays["obs_id"][control_index]),
                float(propensity[treated_index]),
                float(propensity[control_index]),
                float(distance),
            )
            for match_id, (treated_index, control_index, distance) in enumerate(
                zip(treated_indexes, control_indexes, distances, strict=True), start=1
            )
        ]
        _insert_match_rows(con, match_rows)
        _stage(stages, "propensity_matching", stage_start)

        stage_start = time.perf_counter()
        balance_rows: list[dict[str, float | str]] = []
        control_all = np.flatnonzero(~treated_flags)
        treated_all = np.flatnonzero(treated_flags)
        for feature_index, feature_name in enumerate(feature_names):
            values = feature_matrix[:, feature_index]
            balance_rows.append(
                {
                    "variable": feature_name,
                    "smd_before": standardized_mean_difference(
                        values[treated_all], values[control_all]
                    ),
                    "smd_after": standardized_mean_difference(
                        values[treated_indexes], values[control_indexes]
                    ),
                }
            )
        con.execute("DROP TABLE IF EXISTS results.starterpack_growth_balance")
        con.execute(
            """
            CREATE TABLE results.starterpack_growth_balance (
                variable VARCHAR, smd_before DOUBLE, smd_after DOUBLE
            )
            """
        )
        con.executemany(
            "INSERT INTO results.starterpack_growth_balance VALUES (?, ?, ?)",
            [tuple(row.values()) for row in balance_rows],
        )
        export_query(con, "SELECT * FROM results.starterpack_growth_balance", balance_output)

        effect_rows: list[dict[str, Any]] = []
        outcome_pairs = [
            ("new_followers", 7, "post_followers_7"),
            ("new_followers", 30, "post_followers_30"),
            ("new_followers", 90, "post_followers_90"),
            ("new_following", 7, "post_following_7"),
            ("new_following", 30, "post_following_30"),
            ("new_following", 90, "post_following_90"),
        ]
        for outcome, horizon, column in outcome_pairs:
            effect_rows.append(
                {
                    "outcome": outcome,
                    "horizon_days": horizon,
                    "estimand": "matched post-period mean difference",
                    **paired_effect(
                        arrays[column][treated_indexes],
                        arrays[column][control_indexes],
                        arrays["obs_id"][control_indexes],
                    ),
                }
            )
            if outcome == "new_followers":
                treated_values = arrays[column][treated_indexes].astype(np.float64)
                control_values = arrays[column][control_indexes].astype(np.float64)
                cap = float(np.quantile(np.concatenate([treated_values, control_values]), 0.99))
                effect_rows.append(
                    {
                        "outcome": "new_followers_winsorized_p99",
                        "horizon_days": horizon,
                        "estimand": f"matched mean difference; values capped at {cap:g}",
                        **paired_effect(
                            np.minimum(treated_values, cap),
                            np.minimum(control_values, cap),
                            arrays["obs_id"][control_indexes],
                        ),
                    }
                )
                effect_rows.append(
                    {
                        "outcome": "any_new_follower",
                        "horizon_days": horizon,
                        "estimand": "matched probability difference",
                        **paired_effect(
                            (treated_values > 0).astype(np.float64),
                            (control_values > 0).astype(np.float64),
                            arrays["obs_id"][control_indexes],
                        ),
                    }
                )
        did_treated = (
            arrays["post_followers_90"][treated_indexes].astype(np.float64)
            - arrays["pre_followers_90"][treated_indexes].astype(np.float64)
        )
        did_control = (
            arrays["post_followers_90"][control_indexes].astype(np.float64)
            - arrays["pre_followers_90"][control_indexes].astype(np.float64)
        )
        effect_rows.append(
            {
                "outcome": "follower_change_from_prior_90_days",
                "horizon_days": 90,
                "estimand": "matched difference-in-differences",
                **paired_effect(
                    did_treated, did_control, arrays["obs_id"][control_indexes]
                ),
            }
        )
        con.execute("DROP TABLE IF EXISTS results.starterpack_growth_effects")
        con.execute(
            """
            CREATE TABLE results.starterpack_growth_effects (
                outcome VARCHAR, horizon_days INTEGER, estimand VARCHAR, pairs UBIGINT,
                treated_mean DOUBLE, control_mean DOUBLE, mean_difference DOUBLE,
                ci_low DOUBLE, ci_high DOUBLE, standard_error DOUBLE, mean_ratio DOUBLE,
                treated_median DOUBLE, control_median DOUBLE,
                standard_error_method VARCHAR
            )
            """
        )
        con.executemany(
            "INSERT INTO results.starterpack_growth_effects VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [tuple(row.values()) for row in effect_rows],
        )
        export_query(con, "SELECT * FROM results.starterpack_growth_effects", effects_output)

        con.execute("DROP TABLE IF EXISTS results.starterpack_growth_matched_cohort")
        con.execute(
            """
            CREATE TABLE results.starterpack_growth_matched_cohort AS
            SELECT
                m.*,
                t.node_id AS treated_node_id,
                c.node_id AS control_node_id,
                t.index_date AS treated_index_date,
                c.index_date AS control_index_date,
                t.account_age_days AS treated_account_age_days,
                c.account_age_days AS control_account_age_days,
                t.pre_in_degree AS treated_pre_in_degree,
                c.pre_in_degree AS control_pre_in_degree,
                t.pre_out_degree AS treated_pre_out_degree,
                c.pre_out_degree AS control_pre_out_degree,
                t.pre_followers_30 AS treated_pre_followers_30,
                c.pre_followers_30 AS control_pre_followers_30,
                t.pre_followers_90 AS treated_pre_followers_90,
                c.pre_followers_90 AS control_pre_followers_90,
                t.post_followers_7 AS treated_post_followers_7,
                c.post_followers_7 AS control_post_followers_7,
                t.post_followers_30 AS treated_post_followers_30,
                c.post_followers_30 AS control_post_followers_30,
                t.post_followers_90 AS treated_post_followers_90,
                c.post_followers_90 AS control_post_followers_90,
                t.post_following_90 AS treated_post_following_90,
                c.post_following_90 AS control_post_following_90,
                t.packs_on_first_day,
                t.packs_within_30_days,
                t.mean_first_pack_size,
                t.largest_first_pack_size,
                t.self_curated_first_day,
                t.final_community AS treated_final_community,
                c.final_community AS control_final_community,
                c.future_first_pack_date AS control_future_first_pack_date
            FROM growth_matches AS m
            JOIN growth_features AS t ON t.obs_id = m.treated_obs_id
            JOIN growth_features AS c ON c.obs_id = m.control_obs_id
            """
        )
        export_query(
            con,
            "SELECT * FROM results.starterpack_growth_matched_cohort ORDER BY match_id",
            cohort_output,
        )
        _stage(stages, "effects_balance_and_cohort", stage_start)

        stage_start = time.perf_counter()
        subgroup_rows: list[dict[str, Any]] = []
        treated_age = arrays["account_age_days"][treated_indexes].astype(np.float64)
        treated_degree = arrays["pre_in_degree"][treated_indexes].astype(np.float64)
        treated_pack_size = arrays["mean_first_pack_size"][treated_indexes].astype(np.float64)
        treated_pack_count = arrays["packs_within_30_days"][treated_indexes].astype(np.float64)
        subgroup_definitions = {
            "account_age": np.asarray([age_band(value) for value in treated_age]),
            "baseline_followers": np.asarray([degree_band(value) for value in treated_degree]),
            "first_pack_size": np.asarray([pack_size_band(value) for value in treated_pack_size]),
            "packs_within_30_days": np.select(
                [treated_pack_count <= 1, treated_pack_count <= 3],
                ["1 pack", "2-3 packs"],
                default="4+ packs",
            ),
        }
        for dimension, labels in subgroup_definitions.items():
            for label in np.unique(labels):
                selected = labels == label
                if int(selected.sum()) < 200:
                    continue
                for horizon, column in [(30, "post_followers_30"), (90, "post_followers_90")]:
                    subgroup_rows.append(
                        {
                            "dimension": dimension,
                            "subgroup": str(label),
                            "horizon_days": horizon,
                            **paired_effect(
                                arrays[column][treated_indexes[selected]],
                                arrays[column][control_indexes[selected]],
                                arrays["obs_id"][control_indexes[selected]],
                            ),
                        }
                    )
        con.execute("DROP TABLE IF EXISTS results.starterpack_growth_subgroups")
        con.execute(
            """
            CREATE TABLE results.starterpack_growth_subgroups (
                dimension VARCHAR, subgroup VARCHAR, horizon_days INTEGER, pairs UBIGINT,
                treated_mean DOUBLE, control_mean DOUBLE, mean_difference DOUBLE,
                ci_low DOUBLE, ci_high DOUBLE, standard_error DOUBLE, mean_ratio DOUBLE,
                treated_median DOUBLE, control_median DOUBLE,
                standard_error_method VARCHAR
            )
            """
        )
        con.executemany(
            "INSERT INTO results.starterpack_growth_subgroups VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [tuple(row.values()) for row in subgroup_rows],
        )
        export_query(con, "SELECT * FROM results.starterpack_growth_subgroups", subgroups_output)
        _stage(stages, "subgroup_effects", stage_start)

        stage_start = time.perf_counter()
        con.execute(
            """
            CREATE OR REPLACE TEMP TABLE growth_matched_subjects AS
            SELECT m.match_id, 'treated' AS role, t.node_id, t.index_date
            FROM growth_matches AS m JOIN growth_features AS t ON t.obs_id = m.treated_obs_id
            UNION ALL
            SELECT m.match_id, 'control' AS role, c.node_id, c.index_date
            FROM growth_matches AS m JOIN growth_features AS c ON c.obs_id = m.control_obs_id
            """
        )
        con.execute(
            """
            CREATE OR REPLACE TEMP TABLE growth_daily_sparse AS
            SELECT
                s.match_id,
                s.role,
                date_diff('day', s.index_date, f.date_followed)::INTEGER AS relative_day,
                count(*)::UINTEGER AS new_followers
            FROM growth_matched_subjects AS s
            JOIN indexed_follows_by_dst AS f ON f.dst = s.node_id
            WHERE f.date_followed >= s.index_date - INTERVAL 90 DAY
              AND f.date_followed <= s.index_date + INTERVAL 90 DAY
              AND f.date_followed <> s.index_date
            GROUP BY s.match_id, s.role, relative_day
            """
        )
        con.execute("DROP TABLE IF EXISTS results.starterpack_growth_dynamics")
        con.execute(
            """
            CREATE TABLE results.starterpack_growth_dynamics AS
            WITH days AS (
                SELECT unnest(generate_series(-90, 90))::INTEGER AS relative_day
            ), roles(role) AS (VALUES ('treated'), ('control')),
            totals AS (SELECT count(*)::DOUBLE AS matches FROM growth_matches),
            means AS (
                SELECT role, relative_day, sum(new_followers)::DOUBLE AS followers
                FROM growth_daily_sparse GROUP BY role, relative_day
            )
            SELECT
                d.relative_day,
                coalesce(t.followers, 0) / totals.matches AS treated_mean,
                coalesce(c.followers, 0) / totals.matches AS control_mean,
                (coalesce(t.followers, 0) - coalesce(c.followers, 0)) / totals.matches AS mean_difference
            FROM days AS d
            CROSS JOIN totals
            LEFT JOIN means AS t ON t.relative_day = d.relative_day AND t.role = 'treated'
            LEFT JOIN means AS c ON c.relative_day = d.relative_day AND c.role = 'control'
            WHERE d.relative_day <> 0
            ORDER BY d.relative_day
            """
        )
        export_query(con, "SELECT * FROM results.starterpack_growth_dynamics", dynamics_output)
        _stage(stages, "event_time_dynamics", stage_start)

        stage_start = time.perf_counter()
        community_subject_join = (
            "LEFT JOIN results.starterpack_leiden_labels_local AS subject_label ON subject_label.node_id = s.node_id\n"
            "LEFT JOIN results.starterpack_leiden_labels_local AS follower_label ON follower_label.node_id = incoming.follower_id"
            if community_available
            else ""
        )
        same_community_expression = (
            "subject_label.community = follower_label.community"
            if community_available
            else "NULL::BOOLEAN"
        )
        con.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE growth_new_incoming AS
            SELECT
                s.match_id,
                s.role,
                s.node_id,
                s.index_date,
                f.src::UINTEGER AS follower_id,
                min(f.date_followed)::DATE AS follow_date
            FROM growth_matched_subjects AS s
            JOIN indexed_follows_by_dst AS f ON f.dst = s.node_id
            WHERE f.date_followed > s.index_date
              AND f.date_followed <= s.index_date + INTERVAL 90 DAY
            GROUP BY s.match_id, s.role, s.node_id, s.index_date, f.src
            """
        )
        con.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE growth_new_incoming_quality AS
            SELECT
                incoming.match_id,
                incoming.role,
                incoming.follower_id,
                bool_or(reverse_edge.dst IS NOT NULL) AS reciprocal_by_day_90,
                {same_community_expression} AS same_final_community
            FROM growth_new_incoming AS incoming
            JOIN growth_matched_subjects AS s
              ON s.match_id = incoming.match_id AND s.role = incoming.role
            LEFT JOIN indexed_follows_by_src AS reverse_edge
              ON reverse_edge.src = incoming.node_id
             AND reverse_edge.dst = incoming.follower_id
             AND reverse_edge.date_followed >= DATE '{VALID_EVENT_START}'
             AND reverse_edge.date_followed <= incoming.index_date + INTERVAL 90 DAY
            {community_subject_join}
            GROUP BY incoming.match_id, incoming.role, incoming.follower_id,
                     {same_community_expression}
            """
        )
        con.execute("DROP TABLE IF EXISTS results.starterpack_growth_network_quality")
        con.execute(
            """
            CREATE TABLE results.starterpack_growth_network_quality AS
            SELECT
                role,
                count(*)::UBIGINT AS new_followers_90,
                count(*) FILTER (WHERE reciprocal_by_day_90)::UBIGINT AS reciprocal_new_followers_90,
                avg(reciprocal_by_day_90::INTEGER) AS reciprocal_share,
                count(*) FILTER (WHERE same_final_community IS NOT NULL)::UBIGINT AS community_known_pairs,
                avg(same_final_community::INTEGER) FILTER (
                    WHERE same_final_community IS NOT NULL
                ) AS same_final_community_share
            FROM growth_new_incoming_quality
            GROUP BY role
            ORDER BY role
            """
        )
        export_query(con, "SELECT * FROM results.starterpack_growth_network_quality", quality_output)
        network_quality = [
            {
                "role": row[0],
                "new_followers_90": int(row[1]),
                "reciprocal_new_followers_90": int(row[2]),
                "reciprocal_share": float(row[3]) if row[3] is not None else math.nan,
                "community_known_pairs": int(row[4]),
                "same_final_community_share": float(row[5]) if row[5] is not None else math.nan,
            }
            for row in con.execute(
                "SELECT * FROM results.starterpack_growth_network_quality ORDER BY role"
            ).fetchall()
        ]
        _stage(stages, "network_quality", stage_start)

        population_counts = {
            "all_unique_pack_members": int(
                con.execute("SELECT count(*) FROM growth_first_treatment").fetchone()[0]
            ),
            "eligible_treated_users": eligible_treatments,
            "sampled_treated_users": treatment_count,
            "eligible_control_observations": control_count,
            "matched_pairs": len(treated_indexes),
        }

    stage_start = time.perf_counter()
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    horizons = [7, 30, 90]
    follower_effects = [
        row for row in effect_rows if row["outcome"] == "new_followers"
    ]
    follower_effects.sort(key=lambda row: int(row["horizon_days"]))
    figure, axes = plt.subplots(2, 2, figsize=(13, 9))
    positions = np.arange(len(horizons))
    width = 0.36
    axes[0, 0].bar(
        positions - width / 2,
        [row["treated_mean"] for row in follower_effects],
        width,
        label="Starter Pack users",
        color="#2f80ed",
    )
    axes[0, 0].bar(
        positions + width / 2,
        [row["control_mean"] for row in follower_effects],
        width,
        label="Matched controls",
        color="#9aa0a6",
    )
    axes[0, 0].set_xticks(positions, [f"{day} days" for day in horizons])
    axes[0, 0].set_ylabel("Mean new followers")
    axes[0, 0].set_title("Matched follower growth")
    axes[0, 0].legend()
    axes[0, 0].grid(axis="y", alpha=0.25)

    differences = np.asarray([row["mean_difference"] for row in follower_effects])
    lower = np.asarray([row["ci_low"] for row in follower_effects])
    upper = np.asarray([row["ci_high"] for row in follower_effects])
    axes[0, 1].errorbar(
        horizons,
        differences,
        yerr=np.vstack([differences - lower, upper - differences]),
        marker="o",
        capsize=4,
        color="#c0392b",
    )
    axes[0, 1].axhline(0, color="black", linestyle="--", linewidth=1)
    axes[0, 1].set_xlabel("Days after first inclusion")
    axes[0, 1].set_ylabel("Treated - control followers")
    axes[0, 1].set_title("Estimated matched difference (95% CI)")
    axes[0, 1].grid(alpha=0.25)

    with connect(settings, read_only=True) as con:
        dynamics = con.execute(
            "SELECT relative_day, treated_mean, control_mean FROM results.starterpack_growth_dynamics ORDER BY relative_day"
        ).fetchnumpy()
    axes[1, 0].plot(
        dynamics["relative_day"], dynamics["treated_mean"], label="Starter Pack users", color="#2f80ed"
    )
    axes[1, 0].plot(
        dynamics["relative_day"], dynamics["control_mean"], label="Matched controls", color="#9aa0a6"
    )
    axes[1, 0].axvline(0, color="black", linestyle="--", linewidth=1)
    axes[1, 0].set_xlabel("Day relative to first inclusion")
    axes[1, 0].set_ylabel("Mean surviving follow edges created per user")
    axes[1, 0].set_title("Event-time follower dynamics")
    axes[1, 0].legend()
    axes[1, 0].grid(alpha=0.25)

    balance_names = [str(row["variable"]).replace("log1p_", "") for row in balance_rows]
    y_positions = np.arange(len(balance_names))
    axes[1, 1].scatter(
        [abs(float(row["smd_before"])) for row in balance_rows],
        y_positions,
        label="Before matching",
        color="#e67e22",
    )
    axes[1, 1].scatter(
        [abs(float(row["smd_after"])) for row in balance_rows],
        y_positions,
        label="After matching",
        color="#27ae60",
    )
    axes[1, 1].axvline(0.1, color="black", linestyle="--", linewidth=1)
    axes[1, 1].set_yticks(y_positions, balance_names)
    axes[1, 1].set_xlabel("Absolute standardized mean difference")
    axes[1, 1].set_title("Covariate balance")
    axes[1, 1].legend()
    axes[1, 1].grid(axis="x", alpha=0.25)
    figure.suptitle("Starter Pack inclusion and subsequent user growth")
    figure.tight_layout()
    figure_png = FIGURE_OUTPUTS / "starterpack_growth_effect.png"
    figure_pdf = FIGURE_OUTPUTS / "starterpack_growth_effect.pdf"
    figure.savefig(figure_png, dpi=200, bbox_inches="tight")
    figure.savefig(figure_pdf, bbox_inches="tight")
    plt.close(figure)
    supplementary_figures = render_supplementary_figures()
    _stage(stages, "figures", stage_start)

    summary = {
        "complete": True,
        "method": "staggered first-exposure cohort with risk-set propensity matching",
        "causal_claim": False,
        "config": {**asdict(config), "min_treatment_date": config.min_treatment_date.isoformat()},
        "date_coverage": {
            "valid_follow_start": VALID_EVENT_START.isoformat(),
            "follow_data_end": str(data_end),
            "latest_complete_treatment_date": str(last_treatment_date),
            "treatment_day_outcomes_excluded": True,
        },
        "definitions": {
            "treatment": "first date a user was observable in any Starter Pack",
            "control": "user assigned a comparable index date who remained outside all Starter Packs through day 90",
            "primary_outcome": "surviving incoming follow edges dated 1-90 days after index",
            "matching": "nearest propensity score within the same fixed seven-day index-date block and account-age band, with capped replacement",
            "difference_in_differences": "change from prior 90-day follower count relative to the matched control change",
        },
        "population_counts": population_counts,
        "propensity_model": {"features": feature_names, **propensity_diagnostics},
        "matching": matching_diagnostics,
        "balance": balance_rows,
        "effects": effect_rows,
        "network_quality": network_quality,
        "stage_seconds": stages,
        "limitations": [
            "This is an observational matched study and does not establish causality.",
            "The follow file is a snapshot of surviving edges with creation dates, so removed follows are unobserved.",
            "Treatment is first recorded pack inclusion; same-day ordering is unknown, so day zero is excluded.",
            "The treated population is deterministically sampled for safe execution on a 32 GB computer.",
            "Matching balances measured pre-treatment variables but cannot remove unmeasured selection into packs.",
            "Final Leiden communities use later Starter Pack information and are therefore descriptive only, not matching covariates.",
            "Pack type is represented by observable pack size and exposure multiplicity because topical labels are unavailable.",
            "Control matching permits replacement up to the configured cap; reported intervals use control-cluster-robust standard errors.",
        ],
    }
    outputs.extend(
        str(path)
        for path in [
            cohort_output,
            effects_output,
            balance_output,
            dynamics_output,
            subgroups_output,
            quality_output,
            figure_png,
            figure_pdf,
            *supplementary_figures,
        ]
    )
    result = RunResult(
        task="starterpack_growth_effect",
        seconds=time.perf_counter() - started,
        outputs=outputs,
        summary=summary,
    )
    return _write_summary(result)
