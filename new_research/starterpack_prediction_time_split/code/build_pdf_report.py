"""Create the final six-page technical report required by the project prompt."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Image, KeepInFrame, Paragraph, Spacer, Table, TableStyle

from .data_loader import FIGURES, STUDY_ROOT, SUMMARIES


PDF_DIR = STUDY_ROOT / "output" / "pdf"
PDF_PATH = PDF_DIR / "time_split_starterpack_prediction_report.pdf"
SUMMARY_PATH = SUMMARIES / "starterpack_prediction_summary.json"

NAVY = colors.HexColor("#152A46")
BLUE = colors.HexColor("#2F66E8")
TEAL = colors.HexColor("#168C88")
ORANGE = colors.HexColor("#F59E0B")
PALE = colors.HexColor("#EEF4FF")
GRAY = colors.HexColor("#64748B")
RULE = colors.HexColor("#D7DEE8")


def styles():
    fonts = Path("C:/Windows/Fonts")
    pdfmetrics.registerFont(TTFont("Arial", fonts / "arial.ttf"))
    pdfmetrics.registerFont(TTFont("Arial-Bold", fonts / "arialbd.ttf"))
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("title", parent=base["Title"], fontName="Arial-Bold", fontSize=25, leading=29, textColor=colors.white),
        "subtitle": ParagraphStyle("subtitle", parent=base["BodyText"], fontName="Arial", fontSize=11, leading=15, textColor=colors.HexColor("#DCE7FA")),
        "h1": ParagraphStyle("h1", parent=base["Heading1"], fontName="Arial-Bold", fontSize=16, leading=19, textColor=NAVY, spaceAfter=6),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontName="Arial-Bold", fontSize=10.5, leading=13, textColor=BLUE, spaceBefore=5, spaceAfter=3),
        "body": ParagraphStyle("body", parent=base["BodyText"], fontName="Arial", fontSize=8.4, leading=11.4, textColor=NAVY, spaceAfter=4),
        "small": ParagraphStyle("small", parent=base["BodyText"], fontName="Arial", fontSize=6.8, leading=8.8, textColor=GRAY),
        "metric": ParagraphStyle("metric", parent=base["BodyText"], fontName="Arial-Bold", fontSize=15, leading=17, alignment=TA_CENTER, textColor=BLUE),
        "metric_label": ParagraphStyle("metric_label", parent=base["BodyText"], fontName="Arial", fontSize=6.2, leading=7.5, alignment=TA_CENTER, textColor=NAVY),
        "header": ParagraphStyle("header", parent=base["BodyText"], fontName="Arial-Bold", fontSize=6.8, leading=8, alignment=TA_CENTER, textColor=colors.white),
        "cell": ParagraphStyle("cell", parent=base["BodyText"], fontName="Arial", fontSize=6.7, leading=8.4, textColor=NAVY),
        "callout": ParagraphStyle("callout", parent=base["BodyText"], fontName="Arial-Bold", fontSize=9.3, leading=12.5, alignment=TA_CENTER, textColor=NAVY),
    }


def paragraph(text, style):
    return Paragraph(text, style)


def bullet(text, s):
    return Paragraph(f"<font color='#2F66E8'>-</font>&nbsp;&nbsp;{text}", s["body"])


def make_table(data, widths, s, font_size=6.7):
    rows = []
    for row_index, row in enumerate(data):
        converted = []
        for cell in row:
            style = s["header"] if row_index == 0 else s["cell"]
            if row_index > 0 and font_size != 6.7:
                style = style.clone(f"cell-{row_index}")
                style.fontSize = font_size
                style.leading = font_size + 1.7
            converted.append(Paragraph(str(cell), style))
        rows.append(converted)
    result = Table(rows, colWidths=widths, repeatRows=1)
    result.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F7FA")]),
                ("GRID", (0, 0), (-1, -1), 0.35, RULE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return result


def metrics(items, s):
    values = [paragraph(value, s["metric"]) for value, _ in items]
    labels = [paragraph(label, s["metric_label"]) for _, label in items]
    result = Table([values, labels], colWidths=[42 * mm] * len(items))
    result.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), PALE), ("GRID", (0, 0), (-1, -1), 0.4, RULE), ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    return result


def callout(text, s):
    result = Table([[paragraph(text, s["callout"]) ]], colWidths=[168 * mm])
    result.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), PALE), ("BOX", (0, 0), (-1, -1), 0.8, BLUE), ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10), ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    return result


def draw_header_footer(c, page_number):
    width, height = A4
    c.saveState()
    c.setStrokeColor(RULE)
    c.line(18 * mm, height - 12 * mm, width - 18 * mm, height - 12 * mm)
    c.setFont("Arial", 7)
    c.setFillColor(GRAY)
    c.drawString(18 * mm, height - 9 * mm, "TIME-SPLIT STARTER PACK MEMBER PREDICTION")
    c.drawRightString(width - 18 * mm, 9 * mm, f"Page {page_number} of 6")
    c.restoreState()


def add_page(c, page_number, flowables, *, header=True):
    if header:
        draw_header_footer(c, page_number)
    frame = KeepInFrame(174 * mm, 250 * mm, flowables, mode="shrink", hAlign="LEFT", vAlign="TOP")
    width, height = A4
    _, used_height = frame.wrapOn(c, 174 * mm, 250 * mm)
    # Body pages are top-anchored below the running header. The cover keeps its
    # deliberately centered/bottom-balanced composition.
    y = height - 18 * mm - used_height if header else 18 * mm
    frame.drawOn(c, 18 * mm, y)
    c.showPage()


def build() -> Path:
    s = styles()
    summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(PDF_PATH), pagesize=A4)
    c.setTitle("Time-Split Starter Pack Member Prediction")
    c.setAuthor("Final Project Research")
    width, height = A4

    # Page 1: cover and executive result.
    c.setFillColor(NAVY)
    c.rect(0, 0, width, height, fill=1, stroke=0)
    c.setFillColor(BLUE)
    c.rect(0, height - 10 * mm, width, 10 * mm, fill=1, stroke=0)
    c.setFillColor(TEAL)
    c.circle(width - 16 * mm, 16 * mm, 30 * mm, fill=1, stroke=0)
    cover = [
        Spacer(1, 25 * mm),
        paragraph("Time-Split Starter Pack<br/>Member Prediction", s["title"]),
        Spacer(1, 4 * mm),
        paragraph("CPU-only hypergraph learning on the Bluesky A Blue Start dataset", s["subtitle"]),
        Spacer(1, 15 * mm),
        Table([[paragraph("FINAL TECHNICAL REPORT", s["callout"]) ]], colWidths=[70 * mm], style=TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.white), ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7)])),
        Spacer(1, 22 * mm),
        paragraph("<b>Objective.</b> Given a future Starter Pack creator, rank users likely to belong to the pack's initial composition using only graph state available before the prediction periods.", s["subtitle"]),
        Spacer(1, 9 * mm),
        paragraph("<b>Result.</b> On 7,689 untouched test packs, the hybrid model reaches Hit@10 0.639, MRR 0.515, and micro Recall@100 0.176, beating popularity and a hand-built graph heuristic.", s["subtitle"]),
        Spacer(1, 28 * mm),
        paragraph(f"Generated {date.today().strftime('%B %d, %Y')}<br/>Implementation: Python, DuckDB, SciPy, scikit-learn<br/>Hardware target: CPU-only, 12 GB DuckDB cap", s["subtitle"]),
    ]
    add_page(c, 1, cover, header=False)

    # Page 2: formulation, data, split, math.
    split_data = [["Split", "Dates", "Packs", "Initial members", "Eligible"]]
    for row in summary["splits"]:
        split_data.append([row["split"].title(), f"{row['first_date']} to {row['last_date']}", f"{row['packs']:,}", f"{row['positive_members']:,}", f"{100*row['eligibility_rate']:.1f}%"])
    page2 = [
        paragraph("1. Problem, data, and strict time split", s["h1"]),
        paragraph("For a future pack e with known creator c and creation time t, the system ranks candidate users v by a score s(c,v). A positive is a non-creator member recorded on or before t. Later additions are excluded.", s["body"]),
        make_table(split_data, [25*mm, 53*mm, 22*mm, 32*mm, 24*mm], s),
        Spacer(1, 3*mm),
        paragraph("The graph snapshot ends on 2025-01-31. Every learning period begins after a seven-day exclusion gap. All follow, co-membership, degree, popularity, and embedding features stop at the snapshot date. This prevents target packs from shaping their own representation.", s["body"]),
        paragraph("2. Hypergraph representation", s["h1"]),
        paragraph("Users are nodes and historical Starter Packs are hyperedges. For incidence matrix H, node-degree matrix Dv, and hyperedge-size matrix De, the normalized sparse operator is:", s["body"]),
        callout("B = Dv^(-1/2) H De^(-1/2)    (1)<br/>L = I - B B^T                                      (2)", s),
        Spacer(1, 3*mm),
        paragraph("Truncated SVD gives B approximately Uk Sk Vk^T. The 32-dimensional node representation is the row-normalized Uk Sk. By the Eckart-Young-Mirsky theorem, this is the best rank-32 approximation under Frobenius norm. The implementation operates directly on the sparse B and never materializes the dense Laplacian.", s["body"]),
        metrics([("1,684,915", "historical users"), ("296,957", "historical hyperedges"), ("9,762,636", "incidence entries"), ("80.9 MiB", "sparse B")], s),
        Spacer(1, 4*mm),
        paragraph("Data note", s["h2"]),
        paragraph("The local database contains follows, Starter Packs, memberships, and account dates. It has no posts, likes, reposts, replies, or general interaction table; no unavailable feature was fabricated.", s["body"]),
    ]
    add_page(c, 2, page2)

    # Page 3: retrieval and model.
    page3 = [
        paragraph("3. Candidate retrieval and learned ranker", s["h1"]),
        paragraph("Candidates are generated without reading target membership labels. The pool combines pre-cutoff direct follow neighbors, historical co-members, popular users in the creator's embedding cluster, and globally popular historical members. At most 512 candidates are retained per pack.", s["body"]),
        Image(str(FIGURES / "prediction_data_scope.png"), width=170*mm, height=65*mm),
        paragraph("Figure 1. Split volume, historical-user eligibility, and natural retrieval coverage.", s["small"]),
        paragraph("The learned ranker combines embedding cosine, coordinate absolute differences and products, direct-follow flags, shared-pack count, retrieval-source flags, and pre-cutoff degree/popularity features.", s["body"]),
        callout("p(y=1|x) = sigmoid(w^T x + b)                         (3)<br/>Loss = -a_y[y log(p) + (1-y) log(1-p)] + lambda ||w||^2 / 2    (4)<br/>Gradient = a_y(p-y)x + lambda w                         (5)", s),
        Spacer(1, 3*mm),
        paragraph("Features are standardized. A regularized SGD logistic classifier trains for three passes in 200,000-row batches. All retrieved positives and at most 128 deterministic negatives per training pack are used. This is supervised negative sampling, not node2vec skip-gram; node2vec p and q are therefore not applicable.", s["body"]),
        metrics([("4,063,128", "training candidate pairs"), ("79", "model features"), ("3", "streaming epochs"), ("30.61 s", "model training")], s),
    ]
    add_page(c, 3, page3)

    # Page 4: ranking results.
    test = {row["model"]: row for row in summary["evaluation"]["metrics"] if row["split"] == "test"}
    result_data = [["Model", "Hit@10", "MRR", "Micro R@50", "Micro R@100"]]
    for model in ("popularity", "graph_heuristic", "hypergraph_cosine", "hybrid_logistic"):
        row = test[model]
        result_data.append([model.replace("_", " ").title(), f"{row['hit_at_10']:.3f}", f"{row['mrr']:.3f}", f"{row['micro_recall_at_50']:.3f}", f"{row['micro_recall_at_100']:.3f}"])
    page4 = [
        paragraph("4. Untouched test-period results", s["h1"]),
        make_table(result_data, [50*mm, 27*mm, 25*mm, 30*mm, 32*mm], s),
        Spacer(1, 3*mm),
        Image(str(FIGURES / "prediction_test_metrics.png"), width=170*mm, height=47*mm),
        paragraph("Figure 2. End-to-end ranking performance on 7,689 packs created from 2025-08-08 through 2025-09-30.", s["small"]),
        callout("Hybrid test Hit@10 = 0.639    |    MRR = 0.515    |    Micro Recall@100 = 0.176", s),
        Spacer(1, 3*mm),
        paragraph("The hybrid model improves micro Recall@100 by 8.35% relative to the graph heuristic and 19.63% relative to popularity. At K=10, hybrid micro recall is 0.0586 versus 0.0458 for the graph heuristic, a 27.9% relative gain. Validation and test results are similar, so the improvement does not depend on one unusual period.", s["body"]),
        paragraph("Raw hypergraph cosine is weak alone. The useful signal appears when embedding coordinates are combined with direct graph, repeated co-membership, and historical popularity. The strongest learned terms are correlated and should be interpreted as predictive associations, not causal effects.", s["body"]),
        paragraph("Metric definitions", s["h2"]),
        paragraph("Recall@K(e)=|Y_e intersect R_e^K|/|Y_e|. Hit@K is one when at least one true member is in the top K. MRR averages the reciprocal rank of the first true member, with zero for no retrieved positive. All true initial members remain in recall denominators, including cold-start and unretrieved users.", s["body"]),
    ]
    add_page(c, 4, page4)

    # Page 5: bottleneck, scalability, runtime.
    runtime_data = [
        ["Stage", "Measured time"],
        ["Historical out-degree scan", "13.05 s"],
        ["Historical in-degree scan", "123.40 s"],
        ["Creator follow candidates", "102.31 s"],
        ["Hypergraph SVD + clusters", "36.39 s"],
        ["Candidate expansion", "7.66 s"],
        ["Hybrid model training", "30.61 s"],
        ["Validation + test ranking", "7.02 s"],
    ]
    page5 = [
        paragraph("5. Retrieval ceiling, scalability, and runtime", s["h1"]),
        Image(str(FIGURES / "prediction_candidate_sources.png"), width=155*mm, height=78*mm),
        paragraph("Figure 3. Overlapping retrieval sources that contain true future members.", s["small"]),
        paragraph("On test, 80.20% of initial members are historically eligible, but the natural candidate pool retrieves only 21.95% of all positives. Follow neighbors contain most retrieved positives. The hybrid model places 17.64% of all positives in its top 100, capturing about 80% of positives that candidate retrieval makes available. Candidate generation is therefore the dominant remaining bottleneck.", s["body"]),
        paragraph("Memory design", s["h2"]),
        bullet("DuckDB is limited to 12 GB and four threads; temporary spill is confined to a 70 GB study directory.", s),
        bullet("The dense incidence matrix would require about 1.82 TiB in float32; sparse CSR uses 80.9 MiB.", s),
        bullet("The 32-dimensional float32 embedding file uses 205.7 MiB.", s),
        bullet("The 2.416-billion-edge follow relation remains external Parquet and is aggregated in DuckDB.", s),
        paragraph("Measured stage benchmark", s["h2"]),
        make_table(runtime_data, [95*mm, 55*mm], s),
    ]
    add_page(c, 5, page5)

    # Page 6: limitations, conclusion, reproduction.
    page6 = [
        paragraph("6. Limitations and conclusion", s["h1"]),
        bullet("The fixed 2025-01-31 representation is intentionally stale for later packs. This prevents leakage and latent-basis drift but lowers retrieval coverage.", s),
        bullet("About 19.8% of test members do not appear in the historical hypergraph and are true cold-start users.", s),
        bullet("Candidate sources overlap; their positive counts cannot be added as disjoint contributions.", s),
        bullet("The model is observational. Predictive association does not imply that recommending a user would cause pack inclusion.", s),
        bullet("The available local data does not support post, like, reply, or repost features requested in the generic prompt.", s),
        Spacer(1, 4*mm),
        callout("Conclusion: the graph-aware hybrid model consistently beats simple baselines on a strictly later test period. The most valuable next improvement is dynamic, time-local candidate retrieval, followed by inductive cold-start embeddings.", s),
        Spacer(1, 5*mm),
        paragraph("Reproduction", s["h2"]),
        paragraph("From E:\\final_proj:<br/><br/><b>new_research\\starterpack_prediction_time_split\\scripts\\run_study.cmd</b><br/><b>new_research\\starterpack_prediction_time_split\\scripts\\run_tests.cmd</b>", s["body"]),
        paragraph("Primary deliverables", s["h2"]),
        bullet("README.md: complete setup, definitions, equations, line references, scalability notes, and results.", s),
        bullet("outputs/summaries/starterpack_prediction_summary.json: machine-readable experiment result.", s),
        bullet("outputs/parquet/ranking_metrics.parquet and per_pack_ranking_metrics.parquet: aggregate and audit metrics.", s),
        bullet("outputs/parquet/test_top100_recommendations.parquet: final ranked test recommendations.", s),
        bullet("docs/TECHNICAL_REPORT.md: editable report source.", s),
        Spacer(1, 4*mm),
        paragraph("The pipeline is complete, reproducible, memory-bounded, and validated on the full feasible target-pack population rather than a fixed convenience sample.", s["callout"]),
    ]
    add_page(c, 6, page6)
    c.save()

    reader = PdfReader(str(PDF_PATH))
    if len(reader.pages) != 6:
        raise RuntimeError(f"expected exactly 6 pages, found {len(reader.pages)}")
    print(f"[OK] PDF: {PDF_PATH}")
    print(f"[OK] pages: {len(reader.pages)}")
    return PDF_PATH


if __name__ == "__main__":
    build()
