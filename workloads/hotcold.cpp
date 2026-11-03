// Hot/cold page skew, the case memory tiering is built for. One buffer of 64 B lines is first
// touched in order during setup, so with first-touch placement the low addresses fill node 0 and
// the tail spills to node 1. The hot window starts at that tail: when the working set exceeds node
// 0, every hot page begins on the slow node and only promotion (or demotion of the cold pages that
// fill node 0) can fix it. Each access reads one random line: from the hot window with probability
// 1 - c, otherwise from anywhere in the cold rest. Hotness is uniform across the hot window's
// pages, so a page is either hot or cold, never in between.
// Extra options: -h hot window MiB (default 1/8 of -s), -c percent of accesses to the cold rest
// (default 5), -p phases (default 1): the ROI is split into p equal phases and the hot window moves
// one window lower each phase, onto pages that start cold and on node 0, to test how fast
// promotion and demotion follow a shifting hot set.
// Each iter = as many accesses as the buffer has lines. work = accesses.
#include "common.h"

struct HotColdArgs {
    double hot_mb = -1; // < 0: 1/8 of ws
    double cold_pct = 5;
    int phases = 1;
};

static HotColdArgs parse_hotcold(int argc, char** argv) {
    HotColdArgs h;
    for (int i = 1; i + 1 < argc; i += 2) {
        if (!strcmp(argv[i], "-h"))
            h.hot_mb = strtod(argv[i + 1], nullptr);
        else if (!strcmp(argv[i], "-c"))
            h.cold_pct = strtod(argv[i + 1], nullptr);
        else if (!strcmp(argv[i], "-p"))
            h.phases = static_cast<int>(strtol(argv[i + 1], nullptr, 10));
    }
    return h;
}

struct Line {
    uint64_t v;
    uint64_t pad[7];
};

int main(int argc, char** argv) {
    Args a = parse_args(argc, argv);
    HotColdArgs h = parse_hotcold(argc, argv);
    size_t n = a.bytes() / sizeof(Line);
    double hot_mb = h.hot_mb < 0 ? a.ws_mb / 8 : h.hot_mb;
    size_t nh = (size_t)(hot_mb * 1024 * 1024) / sizeof(Line);
    if (nh < 1 || nh >= n || h.phases < 1 || (size_t)h.phases * nh > n || h.cold_pct < 0 ||
        h.cold_pct > 100) {
        fprintf(stderr, "hotcold: need 0 < -h < -s, -p >= 1 with p * h <= s, 0 <= -c <= 100\n");
        return 1;
    }
    Line* buf = (Line*)alloc64(n * sizeof(Line));
    Rng r(a.seed);
    for (size_t i = 0; i < n; i++) { // first touch, in address order
        buf[i].v = r.next();
        for (auto& p : buf[i].pad)
            p = 0;
    }
    // A 32-bit threshold, so the hot/cold choice and the line index come from one RNG draw.
    const uint64_t cold_below = (uint64_t)(h.cold_pct / 100.0 * 4294967296.0);
    const size_t nc = n - nh;
    size_t accesses = n * (size_t)a.iters;
    size_t per_phase = accesses / h.phases;
    uint64_t acc = 0;
    RoiProgress prog(accesses);
    roi_begin();
    for (int ph = 0; ph < h.phases; ph++) {
        size_t lo = n - nh * (ph + 1); // hot window [lo, lo + nh); phase 0 is the tail
        size_t cnt = ph == h.phases - 1 ? accesses - per_phase * ph : per_phase;
        size_t done = per_phase * ph;
        for (size_t c0 = 0; c0 < cnt; c0 += kProgressChunk) {
            size_t c1 = std::min(cnt, c0 + kProgressChunk);
            for (size_t s = c0; s < c1; s++) {
                uint64_t x = r.next();
                uint64_t pick = x & 0xffffffffULL;
                uint64_t k = x >> 32;
                size_t idx;
                if (pick < cold_below) { // anywhere outside the hot window
                    idx = (size_t)(k % nc);
                    if (idx >= lo)
                        idx += nh;
                } else {
                    idx = lo + (size_t)(k % nh);
                }
                acc += buf[idx].v;
            }
            prog.at(done + c1);
        }
    }
    double sec = roi_end();
    report("hotcold", a, sec, (double)accesses, acc);
}
