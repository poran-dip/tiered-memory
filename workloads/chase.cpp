// Dependent pointer chase over one random cycle of 64B nodes. work = hops.
// Each iter = one full cycle (every node visited once). Pure latency-bound.
#include "common.h"
struct Node {
    uint64_t next;
    uint64_t pad[7];
};
int main(int argc, char** argv) {
    Args a = parse_args(argc, argv);
    size_t n = a.bytes() / sizeof(Node);
    Node* nd = (Node*)alloc64(n * sizeof(Node));
    uint64_t* perm = (uint64_t*)alloc64(n * 8);
    Rng r(a.seed);
    for (size_t i = 0; i < n; i++)
        perm[i] = i;
    for (size_t i = n - 1; i > 0; i--)
        std::swap(perm[i], perm[r.next() % i]); // Sattolo: single cycle
    for (size_t i = 0; i < n; i++) {
        nd[i].next = perm[i];
        for (auto& p : nd[i].pad)
            p = 0;
    }
    free(perm);
    // perm is a single-cycle permutation, so following next visits all n nodes.
    uint64_t idx = 0, acc = 0;
    size_t steps = n * (size_t)a.iters;
    RoiProgress prog(steps);
    roi_begin();
    for (size_t lo = 0; lo < steps; lo += kProgressChunk) {
        size_t hi = std::min(steps, lo + kProgressChunk);
        for (size_t s = lo; s < hi; s++) {
            idx = nd[idx].next;
            acc += idx;
        }
        prog.at(hi);
    }
    double sec = roi_end();
    report("chase", a, sec, (double)steps, acc);
}
