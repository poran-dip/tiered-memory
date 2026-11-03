// In-place sort of 16-byte records (key,payload). work = n*log2(n) comparisons (approx).
// Runs a single sort; iters is ignored (re-sorting sorted data is not representative).
// No RoiProgress: std::sort has no loop of ours to report from, and counting in the comparator
// would add instructions to every compare.
#include "common.h"
#include <algorithm>
#include <cmath>
struct Rec {
    uint64_t key, payload;
};
int main(int argc, char** argv) {
    Args a = parse_args(argc, argv);
    size_t n = a.bytes() / sizeof(Rec);
    Rec* d = (Rec*)alloc64(n * sizeof(Rec));
    Rng r(a.seed);
    for (size_t i = 0; i < n; i++)
        d[i] = {r.next(), i};
    roi_begin();
    std::sort(d, d + n, [](const Rec& x, const Rec& y) { return x.key < y.key; });
    double sec = roi_end();
    for (size_t i = 1; i < n; i++)
        if (d[i - 1].key > d[i].key) {
            fprintf(stderr, "NOT SORTED\n");
            return 1;
        }
    uint64_t cs = 0;
    for (size_t i = 0; i < n; i += 1009)
        cs ^= d[i].key + i;
    report("sort", a, sec, n * std::log2((double)n), cs);
}
