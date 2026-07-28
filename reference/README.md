# Upstream reference

The official repository is cloned locally into `reference/upstream-a-blue-start/`
and is ignored by Git to avoid vendoring generated figures and large result files.

Fetch it again with:

```cmd
scripts\fetch_upstream.cmd
```

The local DuckDB implementation recomputes everything that is practical on a
32 GB workstation. Results that require hundreds of gigabytes of graph memory
(full follow SCC/WCC, full clique-projection Leiden, and unfiltered dense
s-line intermediates) are imported with a `reference_` prefix and remain
explicitly marked as official upstream artifacts.

