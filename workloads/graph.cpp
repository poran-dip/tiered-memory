// Graph processing on a synthetic R-MAT (Graph500-style) power-law graph in CSR form:
// one BFS from a high-degree root, then `iters` pull-based PageRank iterations.
// Vertex labels are scrambled so hub vertices are spread across memory, not clustered at the
// start; hubs are then hot, scattered pages and most other vertices are cold.
// The CSR is built in two passes from the same RNG stream (count, then fill), so setup never
// needs more memory than the working set. work = edges traversed.
#include "common.h"
#include <algorithm>

static const uint32_t NONE = ~0u;

int main(int argc, char** argv) {
    Args a = parse_args(argc, argv);
    // Per vertex: offset (8) + PR rank, contrib (4+4) + BFS depth, queue (4+4) = 24 B; edge = 4 B.
    // n is a power of two (R-MAT); the average degree absorbs the rest of the working set.
    size_t total = a.bytes();
    int scale = 1;
    while ((size_t(2) << scale) * (24 + 4 * 16) <= total) // keep average degree >= 16
        scale++;
    const size_t n = size_t(1) << scale;
    size_t m = (total - 24 * n) / 4 & ~size_t(1); // directed edges (undirected pairs x 2)
    if (scale < 8 || m / n < 2) {
        fprintf(stderr, "graph: -s %g MiB too small\n", a.ws_mb);
        return 1;
    }
    const size_t pairs = m / 2;
    const uint64_t mask = n - 1;
    const uint64_t mul = 0x9E3779B97F4A7C15ULL | 1; // odd, so v*mul mod 2^scale is a bijection
    auto scramble = [&](uint64_t v) { return (v * mul + 0x632BE59BD9B4E019ULL) & mask; };
    // R-MAT quadrant probabilities (Graph500 a=0.57, b=c=0.19, d=0.05) in 1/256 steps,
    // one random byte per level so one RNG call covers 8 levels.
    const unsigned QA = 146, QB = QA + 49, QC = QB + 49; // a=.570 b=.191 c=.191 d=.047
    auto edge = [&](Rng& r, uint32_t& u, uint32_t& v) {
        uint64_t x = 0, y = 0, bits = 0;
        for (int b = 0; b < scale; b++) {
            if (b % 8 == 0)
                bits = r.next();
            unsigned p = bits & 0xFF;
            bits >>= 8;
            uint64_t bx = p >= QB, by = (p >= QA && p < QB) || p >= QC;
            x |= bx << b;
            y |= by << b;
        }
        u = (uint32_t)scramble(x);
        v = (uint32_t)scramble(y);
    };

    uint64_t* off = (uint64_t*)alloc64((n + 1) * 8);
    uint32_t* adj = (uint32_t*)alloc64(m * 4);
    float* rank = (float*)alloc64(n * 4);
    float* contrib = (float*)alloc64(n * 4);
    uint32_t* depth = (uint32_t*)alloc64(n * 4);
    uint32_t* queue = (uint32_t*)alloc64(n * 4);

    memset(off, 0, (n + 1) * 8); // pass 1: degrees
    {
        Rng r(a.seed);
        uint32_t u, v;
        for (size_t e = 0; e < pairs; e++) {
            edge(r, u, v);
            off[u + 1]++;
            off[v + 1]++;
        }
    }
    for (size_t i = 0; i < n; i++)
        off[i + 1] += off[i];
    { // pass 2: fill, using depth[] as the per-vertex insert cursor
        for (size_t i = 0; i < n; i++)
            depth[i] = 0;
        Rng r(a.seed);
        uint32_t u, v;
        for (size_t e = 0; e < pairs; e++) {
            edge(r, u, v);
            adj[off[u] + depth[u]++] = v;
            adj[off[v] + depth[v]++] = u;
        }
    }
    uint32_t root = 0;
    for (size_t i = 1; i < n; i++)
        if (off[i + 1] - off[i] > off[root + 1] - off[root])
            root = (uint32_t)i;
    for (size_t i = 0; i < n; i++)
        rank[i] = 1.0f / n;

    double edges = 0;
    // Progress in edges: BFS counts as one pass over the edges (it may reach fewer), then one per
    // PageRank iteration.
    RoiProgress prog((uint64_t)(1 + a.iters) * m);
    roi_begin();
    // BFS (top-down, queue-based)
    for (size_t i = 0; i < n; i++)
        depth[i] = NONE;
    size_t head = 0, tail = 0;
    depth[root] = 0;
    queue[tail++] = root;
    while (head < tail) {
        uint32_t u = queue[head++];
        for (uint64_t e = off[u]; e < off[u + 1]; e++) {
            uint32_t v = adj[e];
            if (depth[v] == NONE) {
                depth[v] = depth[u] + 1;
                queue[tail++] = v;
            }
        }
        edges += off[u + 1] - off[u];
        prog.at((uint64_t)edges);
    }
    // PageRank (pull): rank[v] = (1-d)/n + d * sum_{u in N(v)} rank[u]/deg(u)
    const float d = 0.85f, base = (1.0f - d) / n;
    for (int it = 0; it < a.iters; it++) {
        for (size_t u = 0; u < n; u++) {
            uint64_t deg = off[u + 1] - off[u];
            contrib[u] = deg ? rank[u] / deg : 0.0f;
        }
        for (size_t v = 0; v < n; v++) {
            float s = 0;
            for (uint64_t e = off[v]; e < off[v + 1]; e++)
                s += contrib[adj[e]];
            rank[v] = base + d * s;
            prog.at((uint64_t)(1 + it) * m + off[v + 1]);
        }
        edges += (double)m;
    }
    double sec = roi_end();

    uint64_t cs = tail; // vertices reached
    for (size_t i = 0; i < n; i++)
        if (depth[i] != NONE)
            cs += depth[i];
    double rs = 0;
    for (size_t i = 0; i < n; i++)
        rs += rank[i] * (double)(i % 7 + 1);
    cs = cs * 1000003 + (uint64_t)(rs * 1e9);
    report("graph", a, sec, edges, cs);
}
