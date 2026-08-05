-- Primary 7/30/90-day follower-growth estimates.
SELECT *
FROM results.starterpack_growth_effects
WHERE outcome IN ('new_followers', 'new_followers_winsorized_p99', 'any_new_follower')
ORDER BY horizon_days, outcome;

-- Difference-in-differences estimate.
SELECT *
FROM results.starterpack_growth_effects
WHERE outcome = 'follower_change_from_prior_90_days';

-- Covariate balance. Absolute SMD below 0.10 is the conventional target.
SELECT
    variable,
    smd_before,
    smd_after,
    abs(smd_after) AS absolute_smd_after
FROM results.starterpack_growth_balance
ORDER BY absolute_smd_after DESC;

-- Descriptive subgroup heterogeneity at 90 days.
SELECT
    dimension,
    subgroup,
    pairs,
    treated_mean,
    control_mean,
    mean_difference,
    ci_low,
    ci_high
FROM results.starterpack_growth_subgroups
WHERE horizon_days = 90
ORDER BY dimension, mean_difference DESC;

-- Daily event-time profile around first inclusion.
SELECT *
FROM results.starterpack_growth_dynamics
ORDER BY relative_day;

-- Reciprocity and final-community overlap among new followers.
SELECT *
FROM results.starterpack_growth_network_quality
ORDER BY role;

-- Inspect the most extreme matched-pair differences without changing the estimates.
SELECT
    match_id,
    treated_node_id,
    control_node_id,
    treated_pre_in_degree,
    control_pre_in_degree,
    treated_post_followers_90,
    control_post_followers_90,
    treated_post_followers_90 - control_post_followers_90 AS pair_difference
FROM results.starterpack_growth_matched_cohort
ORDER BY pair_difference DESC
LIMIT 100;
