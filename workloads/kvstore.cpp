// In-memory key-value store, YCSB-style (database / OLTP point operations).
// An open-addressing hash table of 64 B records (8 B key + 56 B value) at load factor 0.75,
// hit by iters * nkeys operations: 95% reads, 5% in-place updates (YCSB workload B).
// Keys are drawn from a scrambled Zipfian distribution with skew -z (default 0.99, YCSB's
// default; -z 0 = uniform), so a small set of hot records is scattered across the whole table.
// work = operations.
#include "common.h"
#include <cmath>

struct Rec {
    uint64_t key;
    uint64_t val[7];
};

static inline uint64_t mix(uint64_t x) { // splitmix64 finalizer
    x ^= x >> 30;
    x *= 0xBF58476D1CE4E5B9ULL;
    x ^= x >> 27;
    x *= 0x94D049BB133111EBULL;
    return x ^ (x >> 31);
}

// Zipfian over [0, n) (Gray et al., as used by YCSB). theta == 0 falls back to uniform.
struct Zipf {
    size_t n;
    double theta, alpha = 0, zetan = 0, eta = 0;
    Zipf(size_t n_, double t) : n(n_), theta(t) {
        if (theta <= 0)
            return;
        for (size_t i = 1; i <= n; i++)
            zetan += 1.0 / std::pow((double)i, theta);
        double zeta2 = 1.0 + 1.0 / std::pow(2.0, theta);
        alpha = 1.0 / (1.0 - theta);
        eta = (1.0 - std::pow(2.0 / n, 1.0 - theta)) / (1.0 - zeta2 / zetan);
    }
    size_t next(Rng& r) {
        double u = (r.next() >> 11) * (1.0 / 9007199254740992.0); // [0,1)
        if (theta <= 0)
            return (size_t)(u * n);
        double uz = u * zetan;
        if (uz < 1.0)
            return 0;
        if (uz < 1.0 + std::pow(0.5, theta))
            return 1;
        size_t k = (size_t)(n * std::pow(eta * u - eta + 1.0, alpha));
        return k < n ? k : n - 1;
    }
};

int main(int argc, char** argv) {
    Args a = parse_args(argc, argv);
    size_t slots = a.bytes() / sizeof(Rec);
    for (size_t p = 1;; p <<= 1) // power of two so the probe uses a mask
        if (p * 2 > slots) {
            slots = p;
            break;
        }
    const size_t mask = slots - 1, nkeys = slots * 3 / 4;
    if (nkeys < 64) {
        fprintf(stderr, "kvstore: -s %g MiB too small\n", a.ws_mb);
        return 1;
    }
    Rec* t = (Rec*)alloc64(slots * sizeof(Rec));
    for (size_t i = 0; i < slots; i++)
        t[i].key = 0; // 0 = empty; real keys are made nonzero below
    auto key_of = [](uint64_t rank) { return mix(rank + 1) | 1; }; // rank -> scrambled key
    for (size_t k = 0; k < nkeys; k++) {
        uint64_t key = key_of(k);
        size_t s = mix(key) & mask;
        while (t[s].key)
            s = (s + 1) & mask;
        t[s].key = key;
        for (int j = 0; j < 7; j++)
            t[s].val[j] = key ^ j;
    }
    Zipf z(nkeys, a.skew);
    Rng r(a.seed);
    size_t ops = nkeys * (size_t)a.iters;
    uint64_t acc = 0, miss = 0;
    RoiProgress prog(ops);
    roi_begin();
    for (size_t lo = 0; lo < ops; lo += kProgressChunk) {
        size_t hi = std::min(ops, lo + kProgressChunk);
        for (size_t o = lo; o < hi; o++) {
            uint64_t key = key_of(z.next(r));
            bool update = r.next() % 100 < 5;
            size_t s = mix(key) & mask;
            while (t[s].key != key && t[s].key)
                s = (s + 1) & mask;
            if (!t[s].key) {
                miss++;
                continue;
            }
            if (update)
                for (uint64_t& v : t[s].val)
                    v += o;
            else
                for (uint64_t v : t[s].val)
                    acc += v;
        }
        prog.at(hi);
    }
    double sec = roi_end();
    if (miss) {
        fprintf(stderr, "kvstore: %llu lookups missed\n", (unsigned long long)miss);
        return 1;
    }
    report("kvstore", a, sec, (double)ops, acc);
}
