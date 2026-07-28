# Raw Data

This directory is intended for the original ICPSR/SOMAR data. Large raw data
files must not be committed to Git.

Expected files:

- `deidentified_nodes.csv.gz`
- `deidentified_nodes.jsonl.gz`
- `deidentified_starterpacks.jsonl.gz`
- `deidentified_starterpack_edgelist.csv.gz`
- `deidentified_starterpack_hif.json.gz`
- `deidentified_follows_edgelist.csv.gz`
- `deidentified_follows_edgelist.parquet`

The path resolver also detects filenames that Windows saved with duplicate
suffixes such as `(1)`. Renaming those files to match the codebook is still
recommended.

The dataset is distributed under CC BY with an additional condition that
prohibits attempts to re-identify users.

