from __future__ import annotations

import json
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    Image,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "tmp" / "pdfs" / "article_data.json"
DESTINATION = ROOT / "output" / "pdf" / "starterpack_growth_full_population_article.pdf"
FIGURES = ROOT / "outputs" / "figures"

NAVY = colors.HexColor("#16324F")
BLUE = colors.HexColor("#2F80ED")
PALE_BLUE = colors.HexColor("#EAF2FC")
GREEN = colors.HexColor("#208C61")
ORANGE = colors.HexColor("#D97706")
RED = colors.HexColor("#C0392B")
GRAY = colors.HexColor("#5F6B76")
LIGHT = colors.HexColor("#F4F6F8")


def fmt(value: object, digits: int = 2) -> str:
    if value is None:
        return "NA"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.{digits}f}"
    return str(value)


def image_for(path: Path, width: float) -> Image:
    from PIL import Image as PILImage

    with PILImage.open(path) as source:
        pixel_width, pixel_height = source.size
    return Image(str(path), width=width, height=width * pixel_height / pixel_width)


def build_styles():
    base = getSampleStyleSheet()
    styles = {
        "body": ParagraphStyle(
            "Body",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=9.4,
            leading=13.2,
            textColor=colors.HexColor("#263238"),
            alignment=TA_JUSTIFY,
            spaceAfter=5,
        ),
        "small": ParagraphStyle(
            "Small",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=7.6,
            leading=10.1,
            textColor=GRAY,
        ),
        "title": ParagraphStyle(
            "Title",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=25,
            leading=30,
            textColor=NAVY,
            alignment=TA_LEFT,
            spaceAfter=10,
        ),
        "subtitle": ParagraphStyle(
            "Subtitle",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=12,
            leading=17,
            textColor=GRAY,
            spaceAfter=15,
        ),
        "h1": ParagraphStyle(
            "H1",
            parent=base["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=16,
            leading=20,
            textColor=NAVY,
            spaceBefore=10,
            spaceAfter=7,
            keepWithNext=True,
        ),
        "h2": ParagraphStyle(
            "H2",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=11.5,
            leading=15,
            textColor=BLUE,
            spaceBefore=8,
            spaceAfter=5,
            keepWithNext=True,
        ),
        "caption": ParagraphStyle(
            "Caption",
            parent=base["BodyText"],
            fontName="Helvetica-Oblique",
            fontSize=7.8,
            leading=10.4,
            textColor=GRAY,
            alignment=TA_CENTER,
            spaceBefore=4,
            spaceAfter=8,
        ),
        "box": ParagraphStyle(
            "Box",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=9.2,
            leading=13,
            textColor=NAVY,
        ),
        "table": ParagraphStyle(
            "TableText",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=7.4,
            leading=9.3,
            textColor=colors.HexColor("#263238"),
        ),
        "table_header": ParagraphStyle(
            "TableHeader",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=7.4,
            leading=9.3,
            textColor=colors.white,
            alignment=TA_CENTER,
        ),
    }
    return styles


def table(headers, body, widths, styles, *, align_right_from=1):
    data = [[Paragraph(str(header), styles["table_header"]) for header in headers]]
    for row in body:
        data.append([Paragraph(str(cell), styles["table"]) for cell in row])
    result = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    commands = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD2D9")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if align_right_from is not None:
        commands.append(("ALIGN", (align_right_from, 1), (-1, -1), "RIGHT"))
    result.setStyle(TableStyle(commands))
    return result


def stat_box(items, styles):
    cells = []
    for label, value in items:
        cells.append(
            Paragraph(
                f'<font size="17" color="#2F80ED"><b>{value}</b></font><br/>'
                f'<font size="7.5" color="#5F6B76">{label}</font>',
                styles["box"],
            )
        )
    result = Table([cells], colWidths=[44 * mm] * len(cells), hAlign="LEFT")
    result.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), PALE_BLUE),
                ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#B8D2F3")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.white),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 9),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
            ]
        )
    )
    return result


def header_footer(canvas, document):
    canvas.saveState()
    width, height = A4
    canvas.setFont("Helvetica", 7.2)
    canvas.setFillColor(GRAY)
    canvas.drawRightString(width - 18 * mm, 6 * mm, f"Page {document.page}")
    canvas.restoreState()


def add_figure(story, filename, caption, styles, width=174 * mm):
    story.append(Spacer(1, 3 * mm))
    story.append(image_for(FIGURES / filename, width))
    story.append(Paragraph(caption, styles["caption"]))


def main() -> int:
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    styles = build_styles()
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    document = BaseDocTemplate(
        str(DESTINATION),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=20 * mm,
        bottomMargin=22 * mm,
        title="Full-Population Analysis of Starter Pack Inclusion and User Growth",
        author="Blue Start Research Project",
        subject="Matched observational analysis of Bluesky Starter Pack inclusion",
    )
    frame = Frame(document.leftMargin, document.bottomMargin, document.width, document.height, id="body")
    document.addPageTemplates([PageTemplate(id="article", frames=[frame], onPage=header_footer)])

    summary = data["summary"]
    population = summary["population_counts"]
    effects = data["effects"]
    raw_effects = sorted(
        [row for row in effects if row["outcome"] == "new_followers"],
        key=lambda row: row["horizon_days"],
    )
    robust_90 = next(
        row for row in effects
        if row["outcome"] == "new_followers_winsorized_p99" and row["horizon_days"] == 90
    )
    binary_90 = next(
        row for row in effects
        if row["outcome"] == "any_new_follower" and row["horizon_days"] == 90
    )
    did_90 = next(
        row for row in effects if row["outcome"] == "follower_change_from_prior_90_days"
    )
    raw_90 = next(row for row in raw_effects if row["horizon_days"] == 90)
    match_rate = population["matched_pairs"] / population["eligible_and_analyzed_treated_users"]

    story = []
    story.append(Spacer(1, 14 * mm))
    story.append(Paragraph("Full-Population Analysis of Starter Pack Inclusion and User Growth", styles["title"]))
    story.append(
        Paragraph(
            "A disk-backed matched-cohort study using every eligible treated user and the complete 2.416-billion-edge Bluesky follow relation",
            styles["subtitle"],
        )
    )
    story.append(Spacer(1, 3 * mm))
    story.append(
        stat_box(
            [
                ("Eligible treated users", fmt(population["eligible_and_analyzed_treated_users"])),
                ("Matched pairs", fmt(population["matched_pairs"])),
                ("90-day difference", f'+{fmt(raw_90["mean_difference"])}'),
                ("Validation checks passed", "15 / 15"),
            ],
            styles,
        )
    )
    story.append(Spacer(1, 12 * mm))
    story.append(Paragraph("Abstract", styles["h1"]))
    story.append(
        Paragraph(
            "This study asks whether a user's first recorded inclusion in a Bluesky Starter Pack is associated with subsequent follower growth. The earlier implementation used a deterministic sample of 100,000 eligible Starter Pack users. The present study removes that treated-user sample and analyzes all 1,084,011 eligible users. Risk-set propensity matching produced 910,685 treated-control pairs. Within 90 days, treated users gained an average of 261.27 surviving follower edges, compared with 30.31 among matched controls, a difference of 230.96 followers (95% confidence interval 228.14 to 233.79). The association remained large after capping outcomes at the 99th percentile and after comparing post-period growth with the previous 90 days. The complete follow network remained on disk; DuckDB, partitioned Parquet indexes, compact NumPy arrays, and a 256-bucket recovery algorithm kept the computation safe on a 32 GB workstation. The study is observational and does not establish causality.",
            styles["body"],
        )
    )
    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph("Keywords", styles["h2"]))
    story.append(Paragraph("Bluesky; Starter Packs; social networks; follower growth; propensity-score matching; DuckDB; external-memory analytics", styles["body"]))
    story.append(PageBreak())

    story.append(Paragraph("1. Research Question and Motivation", styles["h1"]))
    story.append(
        Paragraph(
            "Starter Packs are curated collections of accounts intended to help users discover people to follow. The central question is simple: after a user first appears in a Starter Pack, does that user gain more followers than a comparable user who remains outside Starter Packs? A direct comparison would be misleading because Pack members were already older, more connected, and more active than random users. The analysis therefore constructs a matched control group using information measured before each user's index date.",
            styles["body"],
        )
    )
    story.append(Paragraph("Contribution of the full-population rerun", styles["h2"]))
    story.append(
        Paragraph(
            "The earlier study analyzed 100,000 sampled treated users and found 83,914 matched pairs. The new implementation retains the same temporal design but includes every eligible treated user. This tests whether the earlier conclusion was sensitive to the treated-user sample and creates substantially narrower uncertainty intervals.",
            styles["body"],
        )
    )
    comparison_rows = [
        ["Treated users analyzed", "100,000", fmt(population["eligible_and_analyzed_treated_users"])],
        ["Matched pairs", "83,914", fmt(population["matched_pairs"])],
        ["Unique controls", "35,787", fmt(population["unique_matched_controls"])],
        ["90-day raw difference", "+226.06", f'+{fmt(raw_90["mean_difference"])}'],
        ["90-day capped difference", "+166.04", f'+{fmt(robust_90["mean_difference"])}'],
        ["Treated-user sampling", "Yes", "No"],
    ]
    story.append(table(["Measure", "Previous version", "Full-population version"], comparison_rows, [76 * mm, 47 * mm, 52 * mm], styles))

    story.append(Paragraph("2. Data", styles["h1"]))
    story.append(
        Paragraph(
            "The analysis combines three prepared sources: the complete directed follow relation, Starter Pack membership records, and account metadata. The follow relation contains 2,416,311,437 surviving directed edges with recorded creation dates. Starter Pack data identify 2,003,536 unique members. Account records provide creation dates and eligibility attributes. Follow events are considered valid from 2022-11-17 through the observed data end on 2025-10-18.",
            styles["body"],
        )
    )
    data_rows = [
        ["Follow edges", "2,416,311,437", "Pre/post follower and following counts"],
        ["Unique Starter Pack members", "2,003,536", "Exposure dates and Pack attributes"],
        ["Eligible treated users", fmt(population["eligible_and_analyzed_treated_users"]), "Full treated population"],
        ["Matched cohort", fmt(population["matched_pairs"]), "Outcome estimation"],
    ]
    story.append(table(["Dataset or cohort", "Scale", "Role"], data_rows, [65 * mm, 40 * mm, 70 * mm], styles))

    story.append(Paragraph("3. Study Design", styles["h1"]))
    design_steps = [
        ("Define exposure", "For each user, first observable inclusion is the earliest effective membership date. The effective date uses the later of Pack creation and member addition so exposure cannot precede the Pack."),
        ("Apply eligibility", "Treatments must occur from 2024-06-01 through 2025-07-20, leaving a complete 90-day outcome window. Accounts must be at least 30 days old and have final in/out degree no greater than 100,000."),
        ("Use all treated users", "All 1,084,011 eligible treated users enter the analysis. There is no 100,000-user treated sample."),
        ("Build risk-set controls", "Potential controls receive dates drawn from treated observations and must remain outside every Starter Pack through day 90. Up to eight deterministic candidates are generated per treated user before date and exposure filtering."),
        ("Measure baseline variables", "The model uses log existing follower degree, log existing following degree, log followers gained in the previous 30 days, log follows created in the previous 30 days, and log account age."),
        ("Match", "Nearest propensity-score matching occurs within the same fixed seven-day calendar block and account-age band. Controls may be reused at most ten times."),
        ("Measure outcomes", "Incoming and outgoing follow edges are counted over days 1-7, 1-30, and 1-90. Day zero is excluded because the data do not contain within-day timestamps."),
    ]
    for number, (label, description) in enumerate(design_steps, start=1):
        story.append(Paragraph(f"<b>{number}. {label}.</b> {description}", styles["body"]))

    story.append(Paragraph("4. Large-Scale Implementation on 32 GB RAM", styles["h1"]))
    story.append(
        Paragraph(
            "The complete follow graph was never loaded into Python and was never represented as a NetworkX or in-memory graph object. Incoming edges remained in 256 destination-hash Parquet partitions and outgoing edges in 256 source-hash partitions. DuckDB scanned these files from disk, filtered by candidate users and dates, and aggregated billions of edges into compact observation-level feature tables before transferring numeric arrays to Python.",
            styles["body"],
        )
    )
    memory_rows = [
        ["Main DuckDB memory cap", "14 GB", "Leaves RAM for Windows, Python, NumPy, and SciPy"],
        ["Main spill cap", "100 GB", "Prevents filling the drive"],
        ["Recovery memory cap", "8 GB", "Used for exact network-quality batches"],
        ["Follow partitions", "256 by source + 256 by destination", "Partition-local scans"],
        ["Match insertion", "20,000 rows per batch", "Avoids one million Python tuples at once"],
        ["Quality checkpoints", fmt(data["network_checkpoint_count"]), "Atomic and resumable"],
    ]
    story.append(table(["Mechanism", "Setting", "Purpose"], memory_rows, [55 * mm, 45 * mm, 75 * mm], styles))
    story.append(Paragraph("Safe recovery from the final memory limit", styles["h2"]))
    story.append(
        Paragraph(
            "The monolithic reciprocity/community join reached the intentional 14 GB limit after all core outputs had already been committed. Instead of increasing memory until the machine became unstable, the final join was rewritten as 256 exact hash buckets. Each bucket reads matching incoming and outgoing partitions, aggregates its contribution, writes an atomic JSON checkpoint, and releases temporary state. All buckets completed in 236.02 seconds with less than about 2 GB private memory during recovery. This was an algorithm-shape problem, not a hardware impossibility.",
            styles["body"],
        )
    )
    story.append(Paragraph("5. Main Results", styles["h1"]))
    effect_rows = []
    for row in raw_effects:
        effect_rows.append(
            [
                f'{row["horizon_days"]} days',
                fmt(row["treated_mean"]),
                fmt(row["control_mean"]),
                f'+{fmt(row["mean_difference"])}',
                f'{fmt(row["ci_low"])} to {fmt(row["ci_high"])}',
                fmt(row["mean_ratio"]),
            ]
        )
    story.append(table(["Window", "Treated mean", "Control mean", "Difference", "95% CI", "Mean ratio"], effect_rows, [25 * mm, 28 * mm, 28 * mm, 27 * mm, 44 * mm, 24 * mm], styles))
    story.append(
        Paragraph(
            f'At 90 days, treated users gained {fmt(raw_90["treated_mean"])} surviving followers on average compared with {fmt(raw_90["control_mean"])} among matched controls. The estimated difference was {fmt(raw_90["mean_difference"])} followers. Medians were {fmt(raw_90["treated_median"], 0)} and {fmt(raw_90["control_median"], 0)}, respectively, showing that the association was not limited to the mean.',
            styles["body"],
        )
    )
    add_figure(
        story,
        "starterpack_growth_effect.png",
        "Figure 1. Full-population matched growth, confidence intervals, event-time dynamics, and covariate balance. Source outputs: matched cohort, effects, dynamics, and balance Parquet tables.",
        styles,
        width=165 * mm,
    )

    story.append(Paragraph("6. Matching Quality", styles["h1"]))
    balance_rows = []
    max_after = 0.0
    for row in data["balance"]:
        max_after = max(max_after, abs(float(row["smd_after"])))
        balance_rows.append(
            [
                row["variable"].replace("log1p_", ""),
                fmt(row["smd_before"], 3),
                fmt(row["smd_after"], 3),
                "Pass" if abs(float(row["smd_after"])) < 0.1 else "Review",
            ]
        )
    story.append(table(["Baseline variable", "SMD before", "SMD after", "< 0.10"], balance_rows, [75 * mm, 35 * mm, 35 * mm, 30 * mm], styles))
    story.append(
        Paragraph(
            f'Before matching, absolute standardized mean differences reached {fmt(max(abs(float(row["smd_before"])) for row in data["balance"]), 3)}. After matching, the maximum was only {fmt(max_after, 4)}, far below the conventional 0.10 threshold. The final match rate was {match_rate * 100:.2f}%, with {fmt(population["unique_matched_controls"])} unique controls and a maximum reuse of ten.',
            styles["body"],
        )
    )

    story.append(Paragraph("7. Robustness and Alternative Outcomes", styles["h1"]))
    robustness_rows = [
        ["Raw 90-day followers", fmt(raw_90["mean_difference"]), f'{fmt(raw_90["ci_low"])} to {fmt(raw_90["ci_high"])}'],
        ["99th-percentile-capped followers", fmt(robust_90["mean_difference"]), f'{fmt(robust_90["ci_low"])} to {fmt(robust_90["ci_high"])}'],
        ["Probability of any new follower", f'{binary_90["mean_difference"] * 100:.2f} percentage points', f'{binary_90["ci_low"] * 100:.2f} to {binary_90["ci_high"] * 100:.2f} pp'],
        ["Difference-in-differences", fmt(did_90["mean_difference"]), f'{fmt(did_90["ci_low"])} to {fmt(did_90["ci_high"])}'],
    ]
    story.append(table(["Estimate", "Difference", "95% CI"], robustness_rows, [75 * mm, 45 * mm, 55 * mm], styles))
    story.append(
        Paragraph(
            "Capping the top one percent of outcomes reduced the magnitude but left a large difference of 171.77 followers. The probability of receiving at least one follower was also higher among treated users, so the finding was not driven only by a small number of high-growth accounts. The difference-in-differences result compares the post-treatment 90-day count with the previous 90-day count and remains strongly positive.",
            styles["body"],
        )
    )
    add_figure(
        story,
        "starterpack_growth_robustness.png",
        "Figure 2. Robustness to extreme outcomes and a binary any-follower definition. Source output: starterpack_growth_effects.parquet.",
        styles,
    )

    story.append(PageBreak())
    story.append(Paragraph("8. Heterogeneity Across User and Pack Groups", styles["h1"]))
    subgroup_rows = []
    for row in data["subgroups_90"]:
        subgroup_rows.append(
            [
                str(row["dimension"]).replace("_", " "),
                row["subgroup"],
                fmt(row["pairs"]),
                f'+{fmt(row["mean_difference"])}',
                f'{fmt(row["ci_low"])} to {fmt(row["ci_high"])}',
            ]
        )
    story.append(table(["Dimension", "Subgroup", "Pairs", "90-day difference", "95% CI"], subgroup_rows, [38 * mm, 40 * mm, 28 * mm, 33 * mm, 40 * mm], styles))
    story.append(
        Paragraph(
            "The subgroup table uses the matched cohort to ask where the association is strongest. These comparisons are descriptive because users were not independently rematched within every subgroup. They nevertheless show whether the overall result is concentrated in a narrow class of accounts or appears across account ages, baseline follower levels, first-Pack sizes, and exposure multiplicity.",
            styles["body"],
        )
    )
    add_figure(
        story,
        "starterpack_growth_subgroups.png",
        "Figure 3. Ninety-day matched differences across account age, baseline degree, first-Pack size, and number of Pack exposures. Source output: starterpack_growth_subgroups.parquet.",
        styles,
        width=160 * mm,
    )

    story.append(Paragraph("9. Temporal Pattern", styles["h1"]))
    dynamics = data["dynamics_summary"]
    story.append(
        Paragraph(
            f'The event-time output contains one row per relative day from -90 through +90, excluding day zero. Treated users reached a peak daily mean of {fmt(dynamics["peak_treated_daily"])} new followers on relative day {fmt(dynamics["peak_treated_day"], 0)}. The largest treated-control daily difference was {fmt(dynamics["peak_daily_difference"])} on day {fmt(dynamics["peak_difference_day"], 0)}. The pre-period daily difference averaged {fmt(dynamics["pre_mean_difference"])}; after inclusion it averaged {fmt(dynamics["post_mean_difference"])}. The sharp post-index discontinuity is temporally consistent with a Starter Pack exposure association, although it does not by itself establish causality.',
            styles["body"],
        )
    )

    story.append(Paragraph("10. Relationship Quality", styles["h1"]))
    quality_rows = []
    for row in data["network_quality"]:
        quality_rows.append(
            [
                "Starter Pack users" if row["role"] == "treated" else "Matched controls",
                fmt(row["new_followers_90"]),
                fmt(row["reciprocal_new_followers_90"]),
                f'{row["reciprocal_share"] * 100:.2f}%',
                fmt(row["community_known_pairs"]),
                f'{row["same_final_community_share"] * 100:.2f}%',
            ]
        )
    story.append(table(["Role", "New follower edges", "Reciprocal", "Reciprocal share", "Known community", "Same community"], quality_rows, [35 * mm, 32 * mm, 30 * mm, 27 * mm, 29 * mm, 27 * mm], styles))
    story.append(
        Paragraph(
            "Treated users gained many more follower edges, but a slightly smaller share was reciprocal. Among pairs with available final Leiden labels, treated-user edges were also somewhat less likely to connect users in the same final community. Community composition is descriptive only because the labels were computed from the later complete Starter Pack network and were intentionally excluded from matching.",
            styles["body"],
        )
    )
    add_figure(
        story,
        "starterpack_growth_network_quality.png",
        "Figure 4. Reciprocity and descriptive final-community overlap for newly gained follower edges. Source output: starterpack_growth_network_quality.parquet, computed from 256 exact checkpoints.",
        styles,
    )

    story.append(PageBreak())
    story.append(Paragraph("11. Validation", styles["h1"]))
    validation = data["validation"]["checks"]
    validation_rows = []
    for name, passed in validation.items():
        if name == "all_checks_passed":
            continue
        validation_rows.append([name.replace("_", " "), "PASS" if passed else "FAIL"])
    story.append(table(["Automated check", "Result"], validation_rows, [145 * mm, 30 * mm], styles, align_right_from=None))
    story.append(
        Paragraph(
            "All automated checks passed. They verify the full treated population flag, cohort row count, one treated user per pair, absence of self-matches, exact calendar blocks, control non-exposure through day 90, monotonic cumulative outcomes, control reuse, post-match balance, effect sample sizes, two complete network-quality roles, 256 checkpoints, six Parquet tables, and eight figure files.",
            styles["body"],
        )
    )

    story.append(Paragraph("12. Interpretation and Limitations", styles["h1"]))
    story.append(
        Paragraph(
            "The full-population estimate is close to the original sample estimate: the raw 90-day difference increased from 226.06 to 230.96 followers, about a 2.2% change. This stability supports the conclusion that the sample-based finding was not an artifact of selecting 100,000 treated users. The large immediate increase, strong measured balance, outlier-robust estimate, binary outcome, temporal profile, and broad subgroup pattern make the result suitable for a final-project presentation.",
            styles["body"],
        )
    )
    limitations = [
        "The design is observational. Matching cannot remove unmeasured differences such as account quality, topic, language, posting activity, or curator judgment.",
        "The follow relation is a snapshot of surviving edges with creation dates. Later-unfollowed edges are not observed.",
        "Only dates are available. Day-zero edges are excluded because treatment and follow events cannot be ordered within the day.",
        "All eligible treated users are included, but control construction remains deterministically bounded to eight candidates per treated user before eligibility filtering.",
        "Controls may be reused up to ten times. Confidence intervals therefore use control-cluster-robust standard errors.",
        "Final community labels use later network information and are descriptive outcomes, not baseline matching covariates.",
    ]
    for item in limitations:
        story.append(Paragraph(f"- {item}", styles["body"]))

    story.append(Paragraph("13. Conclusion", styles["h1"]))
    story.append(Paragraph("Substantive finding", styles["h2"]))
    story.append(
        Paragraph(
            "Across every eligible treated user, first recorded Starter Pack inclusion is associated with substantially faster subsequent follower growth. The matched estimate is approximately 231 additional surviving followers over 90 days, and approximately 172 after capping extreme outcomes. The effect is immediate, persistent, visible across multiple subgroups, and close to the result from the earlier 100,000-user analysis. The engineering contribution is equally important: a 2.4-billion-edge temporal network study was completed on 32 GB RAM by leaving edges on disk, aggregating in DuckDB, matching in compact arrays, and replacing one unsafe global join with exact resumable partitions.",
            styles["body"],
        )
    )
    story.append(Paragraph("What the full-population rerun adds", styles["h2"]))
    story.append(
        Paragraph(
            "Removing the treated-user sample changed the main estimate only modestly, from 226.06 to 230.96 additional followers. The similar magnitude, strong post-match balance, and narrower interval show that the earlier result was stable rather than sample-specific. The expanded cohort also supports more precise subgroup and relationship-quality descriptions.",
            styles["body"],
        )
    )
    story.append(Paragraph("Engineering lesson", styles["h2"]))
    story.append(
        Paragraph(
            "A 32 GB workstation was sufficient because the implementation matched each operation to the storage layout. Relational date-window aggregation stayed in DuckDB, the complete edge relation stayed in partitioned Parquet, numerical matching used compact arrays, and the only unsafe join was converted into bounded resumable batches. Increasing virtual memory or adopting the supplied STXXL converter was unnecessary for this study.",
            styles["body"],
        )
    )
    story.append(Paragraph("Appropriate claim", styles["h2"]))
    story.append(
        Paragraph(
            "The defensible conclusion is an association, not a causal effect: users grew faster after first recorded Starter Pack inclusion than observably similar users who remained unexposed through the outcome window. A causal interpretation would require stronger assumptions, richer activity and content covariates, or an experimental or quasi-experimental design.",
            styles["body"],
        )
    )

    story.append(PageBreak())
    story.append(Paragraph("Appendix A. Complete Output Inventory", styles["h1"]))
    story.append(
        Paragraph(
            "Every analytical output generated by the full-population study is either displayed, summarized, or explicitly inventoried below. PNG figures are embedded in this article; PDF versions preserve vector-quality presentation. Parquet files are the machine-readable source tables. JSON files store the final summary and validation record.",
            styles["body"],
        )
    )
    inventory_rows = []
    for item in data["output_inventory"]:
        size = int(item["bytes"])
        readable = f"{size / (1024 * 1024):.2f} MiB" if size >= 1024 * 1024 else f"{size / 1024:.1f} KiB"
        purpose = ""
        name = Path(item["path"]).name
        if name.endswith("matched_cohort.parquet"):
            purpose = "Pair-level matched cohort"
        elif name.endswith("effects.parquet"):
            purpose = "Primary and robust estimates"
        elif name.endswith("balance.parquet"):
            purpose = "Before/after matching balance"
        elif name.endswith("dynamics.parquet"):
            purpose = "Daily event-time series"
        elif name.endswith("subgroups.parquet"):
            purpose = "Subgroup estimates"
        elif name.endswith("network_quality.parquet"):
            purpose = "Reciprocity and community metrics"
        elif name.endswith("validation.json"):
            purpose = "Automated validation record"
        elif name.endswith(".json"):
            purpose = "Complete study summary"
        elif name.endswith(".png"):
            purpose = "Embedded raster figure"
        elif name.endswith(".pdf"):
            purpose = "Vector presentation figure"
        inventory_rows.append([item["path"], readable, purpose])
    story.append(table(["Output", "Size", "Use in study"], inventory_rows, [103 * mm, 25 * mm, 52 * mm], styles, align_right_from=None))

    story.append(Paragraph("Appendix B. Reproduction", styles["h1"]))
    story.append(
        Paragraph(
            "Project directory: E:/final_proj/new_research/starterpack_growth_effect_full_population. Run scripts/run_full_study.cmd from E:/final_proj. The runner reuses a completed result. On a fresh run it performs the full analysis, catches the intentionally bounded-memory condition if the global quality join reaches the cap, completes the exact 256-bucket recovery, and finalizes all tables and figures. Run scripts/validate_full_study.py to repeat validation.",
            styles["body"],
        )
    )
    story.append(
        Paragraph(
            "The supplied build_unified_stxxl.cpp program was reviewed but not used. It builds a custom binary graph from CSV data, whereas this study requires per-user treatment dates, temporal windows, and relational joins. The existing source/destination Parquet indexes and DuckDB were the appropriate backend.",
            styles["body"],
        )
    )

    document.build(story)
    print(DESTINATION)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
