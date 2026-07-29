#define _WIN32_WINNT 0x0600

#include <igraph.h>
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


int main(int argc, char **argv) {
    if (argc != 5) {
        fprintf(
            stderr,
            "Usage: leiden32 EDGE_FILE NODE_COUNT EDGE_COUNT OUTPUT_FILE\n"
        );
        return 2;
    }
    if (sizeof(igraph_integer_t) != 4) {
        return fail("This executable must use a 32-bit-integer igraph build.");
    }

    const char *edge_path = argv[1];
    const igraph_integer_t node_count =
        (igraph_integer_t) strtoul(argv[2], NULL, 10);
    const igraph_integer_t edge_count =
        (igraph_integer_t) strtoul(argv[3], NULL, 10);
    const char *output_path = argv[4];
    const igraph_integer_t endpoint_count = 2 * edge_count;

    FILE *input = fopen(edge_path, "rb");
    if (!input) {
        return fail("Could not open the binary edge file.");
    }

    igraph_vector_int_t edges;
    if (igraph_vector_int_init(&edges, endpoint_count) != IGRAPH_SUCCESS) {
        fclose(input);
        return fail("Could not allocate the edge vector.");
    }
    ULONGLONG started = GetTickCount64();
    size_t read_count = fread(
        VECTOR(edges),
        sizeof(igraph_integer_t),
        (size_t) endpoint_count,
        input
    );
    fclose(input);
    if (read_count != (size_t) endpoint_count) {
        igraph_vector_int_destroy(&edges);
        return fail("The binary edge file ended before the expected edge count.");
    }
    printf(
        "[NATIVE32] edges_loaded seconds=%.3f nodes=%d edges=%d\n",
        elapsed_seconds(started),
        (int) node_count,
        (int) edge_count
    );
    fflush(stdout);

    igraph_t graph;
    started = GetTickCount64();
    if (
        igraph_create(&graph, &edges, node_count, IGRAPH_UNDIRECTED)
        != IGRAPH_SUCCESS
    ) {
        igraph_vector_int_destroy(&edges);
        return fail("igraph_create failed.");
    }
    igraph_vector_int_destroy(&edges);
    printf(
        "[NATIVE32] graph_ready seconds=%.3f\n",
        elapsed_seconds(started)
    );
    fflush(stdout);

    igraph_vector_int_t degrees;
    igraph_vector_t vertex_weights;
    igraph_vector_int_t membership;
    if (
        igraph_vector_int_init(&degrees, node_count) != IGRAPH_SUCCESS
        || igraph_vector_init(&vertex_weights, node_count) != IGRAPH_SUCCESS
        || igraph_vector_int_init(&membership, 0) != IGRAPH_SUCCESS
    ) {
        igraph_destroy(&graph);
        return fail("Could not allocate Leiden working vectors.");
    }
    if (
        igraph_degree(
            &graph,
            &degrees,
            igraph_vss_all(),
            IGRAPH_ALL,
            IGRAPH_LOOPS
        ) != IGRAPH_SUCCESS
    ) {
        return fail("Could not calculate graph degrees.");
    }
    for (igraph_integer_t node = 0; node < node_count; node++) {
        VECTOR(vertex_weights)[node] = VECTOR(degrees)[node];
    }
    igraph_vector_int_destroy(&degrees);

    if (igraph_rng_seed(igraph_rng_default(), 0) != IGRAPH_SUCCESS) {
        return fail("Could not set the igraph random seed.");
    }
    igraph_integer_t community_count = 0;
    igraph_real_t quality = 0.0;
    const igraph_real_t resolution = 1.0 / (2.0 * edge_count);
    printf(
        "[NATIVE32] leiden_start resolution=%.15g beta=0.01 iterations=2 seed=0\n",
        resolution
    );
    fflush(stdout);
    started = GetTickCount64();
    if (
        igraph_community_leiden(
            &graph,
            NULL,
            &vertex_weights,
            resolution,
            0.01,
            0,
            2,
            &membership,
            &community_count,
            &quality
        ) != IGRAPH_SUCCESS
    ) {
        return fail("igraph_community_leiden failed.");
    }
    double leiden_seconds = elapsed_seconds(started);

    igraph_real_t modularity = 0.0;
    if (
        igraph_modularity(
            &graph,
            &membership,
            NULL,
            1.0,
            0,
            &modularity
        ) != IGRAPH_SUCCESS
    ) {
        return fail("Could not calculate final modularity.");
    }
    printf(
        "[NATIVE32] leiden_ready seconds=%.3f communities=%d quality=%.12f "
        "modularity=%.12f\n",
        leiden_seconds,
        (int) community_count,
        quality,
        modularity
    );
    fflush(stdout);

    FILE *output = fopen(output_path, "wb");
    if (!output) {
        return fail("Could not open the membership output file.");
    }
    size_t written = fwrite(
        VECTOR(membership),
        sizeof(igraph_integer_t),
        (size_t) node_count,
        output
    );
    fclose(output);
    if (written != (size_t) node_count) {
        return fail("Could not write the complete membership vector.");
    }

    igraph_vector_int_destroy(&membership);
    igraph_vector_destroy(&vertex_weights);
    igraph_destroy(&graph);
    return 0;
}
