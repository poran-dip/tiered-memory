// Database hash join (OLAP), no-partitioning: build a hash table on R, then probe it with S.
// R and S are arrays of 16 B tuples (key, payload); every S tuple's key matches exactly one R
// tuple, uniformly at random. Build is random writes into the table; probe streams S
// sequentially while making random reads into the table. Both phases are in the ROI and run
// once per iter (the table is rebuilt each time). Working set: table 1/6-1/3 (power of two,
// load 0.5), R half the table, S the rest (|S| >= 3|R|). work = tuples processed (|R| + |S| per
// iter).
#include "common.h"

struct Tup {
    uint64_t key, payload;
};

static inline uint64_t mix(uint64_t x) { // splitmix64 finalizer
    x ^= x >> 30;
    x *= 0xBF58476D1CE4E5B9ULL;
    x ^= x >> 27;
    x *= 0x94D049BB133111EBULL;
    return x ^ (x >> 31);
}

int main(int argc, char** argv) {
    Args a = parse_args(argc, argv);
    size_t total = a.bytes();
    size_t slots = 1; // 2 slots per R tuple; largest power of two with the table <= 1/3 of ws
    while (slots * 2 * sizeof(Tup) <= total / 3)
        slots <<= 1;
    const size_t mask = slots - 1;
    const size_t nr = slots / 2;
    size_t ns = (total - slots * sizeof(Tup) - nr * sizeof(Tup)) / sizeof(Tup);
    if (nr < 64 || ns < nr) {
        fprintf(stderr, "hashjoin: -s %g MiB too small\n", a.ws_mb);
        return 1;
    }
    Tup* R = (Tup*)alloc64(nr * sizeof(Tup));
    Tup* S = (Tup*)alloc64(ns * sizeof(Tup));
    Tup* H = (Tup*)alloc64(slots * sizeof(Tup));
    Rng r(a.seed);
    for (size_t i = 0; i < nr; i++)
        R[i] = {mix(i + 1) | 1, i}; // distinct nonzero keys; 0 marks an empty slot
    for (size_t i = 0; i < ns; i++) {
        size_t j = r.next() % nr;
        S[i] = {R[j].key, i};
    }
    uint64_t acc = 0, matches = 0;
    RoiProgress prog((uint64_t)a.iters * (nr + ns)); // tuples: R built, then S probed
    roi_begin();
    for (int it = 0; it < a.iters; it++) {
        size_t done = (size_t)it * (nr + ns);
        for (size_t s = 0; s < slots; s++)
            H[s].key = 0;
        for (size_t lo = 0; lo < nr; lo += kProgressChunk) { // build
            size_t hi = std::min(nr, lo + kProgressChunk);
            for (size_t i = lo; i < hi; i++) {
                size_t s = mix(R[i].key) & mask;
                while (H[s].key)
                    s = (s + 1) & mask;
                H[s] = R[i];
            }
            prog.at(done + hi);
        }
        for (size_t lo = 0; lo < ns; lo += kProgressChunk) { // probe
            size_t hi = std::min(ns, lo + kProgressChunk);
            for (size_t i = lo; i < hi; i++) {
                uint64_t k = S[i].key;
                size_t s = mix(k) & mask;
                while (H[s].key && H[s].key != k)
                    s = (s + 1) & mask;
                if (H[s].key == k) {
                    acc += H[s].payload ^ S[i].payload;
                    matches++;
                }
            }
            prog.at(done + nr + hi);
        }
    }
    double sec = roi_end();
    const size_t expected = ns * (size_t)a.iters;
    if (matches != expected) {
        fprintf(stderr, "hashjoin: %llu matches, expected %llu\n", (unsigned long long)matches,
                (unsigned long long)expected);
        return 1;
    }
    report("hashjoin", a, sec, (double)(nr + ns) * a.iters, acc);
}
