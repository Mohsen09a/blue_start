#define _WIN32_WINNT 0x0600

#include <inttypes.h>
#include <omp.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <windows.h>


static double elapsed_seconds(ULONGLONG started) {
    return (GetTickCount64() - started) / 1000.0;
}


static int fail(const char *message) {
    fprintf(stderr, "%s\n", message);
    return 1;
}


static void *read_array(
    const char *path,
    size_t item_size,
    uint64_t item_count
) {
    FILE *handle = fopen(path, "rb");
    if (!handle) {
        return NULL;
    }
    if (item_count > SIZE_MAX / item_size) {
        fclose(handle);
        return NULL;
    }
    void *data = malloc((size_t) item_count * item_size);
    if (!data) {
        fclose(handle);
        return NULL;
    }
    size_t read_count = fread(data, item_size, (size_t) item_count, handle);
    fclose(handle);
    if (read_count != (size_t) item_count) {
        free(data);
        return NULL;
    }
    return data;
}


static int write_array(
    const char *path,
    const void *data,
    size_t item_size,
    size_t item_count
) {
    FILE *handle = fopen(path, "wb");
    if (!handle) {
        return 0;
    }
    size_t written = fwrite(data, item_size, item_count, handle);
    int closed = fclose(handle);
    return written == item_count && closed == 0;
}


int main(int argc, char **argv) {
    if (argc != 13) {
        fprintf(
            stderr,
            "Usage: sline_full EDGE_OFFSETS EDGE_NODES NODE_OFFSETS "
            "NODE_EDGES PACK_COUNT NODE_COUNT INCIDENCE_COUNT START_PACK "
            "END_PACK S_MAX HIST_OUTPUT MAX_OUTPUT\n"
        );
        return 2;
    }

    const char *edge_offsets_path = argv[1];
    const char *edge_nodes_path = argv[2];
    const char *node_offsets_path = argv[3];
    const char *node_edges_path = argv[4];
    const uint32_t pack_count = (uint32_t) strtoul(argv[5], NULL, 10);
    const uint32_t node_count = (uint32_t) strtoul(argv[6], NULL, 10);
    const uint64_t incidence_count = _strtoui64(argv[7], NULL, 10);
    const uint32_t start_pack = (uint32_t) strtoul(argv[8], NULL, 10);
    const uint32_t end_pack = (uint32_t) strtoul(argv[9], NULL, 10);
    const uint32_t s_max = (uint32_t) strtoul(argv[10], NULL, 10);
    const char *hist_output = argv[11];
    const char *max_output = argv[12];

    if (
        pack_count == 0 || node_count == 0 || incidence_count == 0
        || start_pack >= end_pack || end_pack > pack_count || s_max == 0
        || s_max > UINT16_MAX
    ) {
        return fail("Invalid graph dimensions or pack range.");
    }

    ULONGLONG total_started = GetTickCount64();
    int64_t *edge_offsets = read_array(
        edge_offsets_path,
        sizeof(int64_t),
        (uint64_t) pack_count + 1
    );
    uint32_t *edge_nodes = read_array(
        edge_nodes_path,
        sizeof(uint32_t),
        incidence_count
    );
    int64_t *node_offsets = read_array(
        node_offsets_path,
        sizeof(int64_t),
        (uint64_t) node_count + 1
    );
    uint32_t *node_edges = read_array(
        node_edges_path,
        sizeof(uint32_t),
        incidence_count
    );
    if (!edge_offsets || !edge_nodes || !node_offsets || !node_edges) {
        free(edge_offsets);
        free(edge_nodes);
        free(node_offsets);
        free(node_edges);
        return fail("Could not read the CSR input arrays.");
    }
    printf(
        "[SLINE] input_ready seconds=%.3f range=%" PRIu32 ":%" PRIu32
        " packs=%" PRIu32 " nodes=%" PRIu32 " incidences=%" PRIu64
        " threads=%d\n",
        elapsed_seconds(total_started),
        start_pack,
        end_pack,
        pack_count,
        node_count,
        incidence_count,
        omp_get_max_threads()
    );
    fflush(stdout);

    uint64_t *global_hist = calloc((size_t) s_max + 1, sizeof(uint64_t));
    uint16_t *global_max = calloc(pack_count, sizeof(uint16_t));
    if (!global_hist || !global_max) {
        free(edge_offsets);
        free(edge_nodes);
        free(node_offsets);
        free(node_edges);
        free(global_hist);
        free(global_max);
        return fail("Could not allocate output arrays.");
    }

    volatile int allocation_failed = 0;
    volatile int overlap_overflow = 0;
    uint32_t completed = 0;
    ULONGLONG compute_started = GetTickCount64();

    #pragma omp parallel
    {
        uint16_t *counts = calloc(pack_count, sizeof(uint16_t));
        uint32_t *touched = malloc((size_t) pack_count * sizeof(uint32_t));
        uint16_t *local_max = calloc(pack_count, sizeof(uint16_t));
        uint64_t *local_hist = calloc(
            (size_t) s_max + 1,
            sizeof(uint64_t)
        );
        if (!counts || !touched || !local_max || !local_hist) {
            #pragma omp atomic write
            allocation_failed = 1;
        }

        #pragma omp barrier
        if (!allocation_failed) {
            #pragma omp for schedule(dynamic, 8)
            for (uint32_t pack = start_pack; pack < end_pack; ++pack) {
                uint32_t touched_count = 0;
                for (
                    int64_t position = edge_offsets[pack];
                    position < edge_offsets[pack + 1];
                    ++position
                ) {
                    uint32_t node = edge_nodes[position];
                    for (
                        int64_t location = node_offsets[node];
                        location < node_offsets[node + 1];
                        ++location
                    ) {
                        uint32_t other = node_edges[location];
                        if (other <= pack) {
                            continue;
                        }
                        if (counts[other] == 0) {
                            touched[touched_count++] = other;
                        }
                        if (counts[other] == UINT16_MAX) {
                            #pragma omp atomic write
                            overlap_overflow = 1;
                        } else {
                            counts[other] += 1;
                        }
                    }
                }

                uint16_t pack_maximum = 0;
                for (uint32_t index = 0; index < touched_count; ++index) {
                    uint32_t other = touched[index];
                    uint16_t overlap = counts[other];
                    uint16_t clipped = overlap > s_max
                        ? (uint16_t) s_max
                        : overlap;
                    local_hist[clipped] += 1;
                    if (clipped > pack_maximum) {
                        pack_maximum = clipped;
                    }
                    if (clipped > local_max[other]) {
                        local_max[other] = clipped;
                    }
                    counts[other] = 0;
                }
                if (pack_maximum > local_max[pack]) {
                    local_max[pack] = pack_maximum;
                }

                uint32_t done;
                #pragma omp atomic capture
                done = ++completed;
                if (done % 512 == 0 || done == end_pack - start_pack) {
                    #pragma omp critical(progress_output)
                    {
                        printf(
                            "[SLINE] progress=%" PRIu32 "/%" PRIu32
                            " seconds=%.3f\n",
                            done,
                            end_pack - start_pack,
                            elapsed_seconds(compute_started)
                        );
                        fflush(stdout);
                    }
                }
            }

            #pragma omp critical(result_merge)
            {
                for (uint32_t s = 1; s <= s_max; ++s) {
                    global_hist[s] += local_hist[s];
                }
                for (uint32_t pack = 0; pack < pack_count; ++pack) {
                    if (local_max[pack] > global_max[pack]) {
                        global_max[pack] = local_max[pack];
                    }
                }
            }
        }
        free(counts);
        free(touched);
        free(local_max);
        free(local_hist);
    }

    free(edge_offsets);
    free(edge_nodes);
    free(node_offsets);
    free(node_edges);
    if (allocation_failed) {
        free(global_hist);
        free(global_max);
        return fail("A worker could not allocate its bounded work arrays.");
    }
    if (overlap_overflow) {
        free(global_hist);
        free(global_max);
        return fail("A pack overlap exceeded the uint16 counter capacity.");
    }

    if (
        !write_array(
            hist_output,
            global_hist,
            sizeof(uint64_t),
            (size_t) s_max + 1
        )
        || !write_array(
            max_output,
            global_max,
            sizeof(uint16_t),
            pack_count
        )
    ) {
        free(global_hist);
        free(global_max);
        return fail("Could not write a complete result file.");
    }

    uint64_t distinct_pairs = 0;
    for (uint32_t s = 1; s <= s_max; ++s) {
        distinct_pairs += global_hist[s];
    }
    printf(
        "[SLINE] complete compute_seconds=%.3f total_seconds=%.3f "
        "distinct_pairs=%" PRIu64 "\n",
        elapsed_seconds(compute_started),
        elapsed_seconds(total_started),
        distinct_pairs
    );
    fflush(stdout);
    free(global_hist);
    free(global_max);
    return 0;
}
