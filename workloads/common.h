// Shared harness: args, RNG, ROI markers, result line.
#pragma once
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <utility>
#if defined(USE_M5) || defined(USE_M5_FS)
extern "C" {
#include <gem5/m5ops.h>
}
#endif
#ifdef USE_M5_FS
// Full-system build (make m5-fs). The guest may start on the KVM CPU, which cannot trap gem5's
// special instruction, so m5ops go through gem5's memory-mapped region instead (address mode).
// map_m5_mem() lives in libm5.a (util/m5/src/m5_mmap.c) but its header is not installed; it opens
// /dev/gem5_bridge (world-writable in the gem5 Ubuntu images) and falls back to /dev/mem.
extern "C" void map_m5_mem();
// Hypercall numbers handled by gem5's exit handlers (src/python/gem5/simulate/exit_handler.py).
constexpr uint64_t kHypercallRoiBegin = 4;
constexpr uint64_t kHypercallRoiEnd = 5;
#endif

struct Args {
    double ws_mb = 256;
    size_t bytes() const { return (size_t)(ws_mb * 1024 * 1024); }
    int iters = 1;
    uint64_t seed = 42;
    double skew = 0.99; // -z: Zipfian skew for workloads that draw keys (kvstore); 0 = uniform
};

inline Args parse_args(int argc, char** argv) {
    Args a;
    for (int i = 1; i + 1 < argc; i += 2) {
        if (!strcmp(argv[i], "-s"))
            a.ws_mb = strtod(argv[i + 1], nullptr);
        else if (!strcmp(argv[i], "-i"))
            a.iters = static_cast<int>(strtol(argv[i + 1], nullptr, 10));
        else if (!strcmp(argv[i], "-r"))
            a.seed = strtoull(argv[i + 1], nullptr, 10);
        else if (!strcmp(argv[i], "-z"))
            a.skew = strtod(argv[i + 1], nullptr);
    }
    return a;
}

struct Rng {
    uint64_t s;
    explicit Rng(uint64_t x) : s(x ? x : 88172645463325252ULL) {}
    uint64_t next() {
        s ^= s << 13;
        s ^= s >> 7;
        s ^= s << 17;
        return s;
    }
    float unit() { return (next() >> 40) * (1.0f / 16777216.0f); } // [0,1)
};

inline double now_sec() {
    timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec * 1e-9;
}

inline double g_t0 = 0;
// SE build: stats are reset at ROI begin and dumped at ROI end, so block 1 of stats.txt is the ROI.
// FS build: the same happens in the config's exit handlers, which also switch the CPU model
// (KVM outside the ROI, timing/O3 inside it).
inline void roi_begin() {
#ifdef USE_M5
    m5_reset_stats(0, 0);
#endif
#ifdef USE_M5_FS
    map_m5_mem();
    m5_hypercall_addr(kHypercallRoiBegin);
#endif
    g_t0 = now_sec();
}
inline double roi_end() {
    double t = now_sec() - g_t0;
#ifdef USE_M5
    m5_dump_reset_stats(0, 0);
#endif
#ifdef USE_M5_FS
    m5_hypercall_addr(kHypercallRoiEnd);
#endif
    return t;
}

// work = workload-defined unit (bytes, flops, steps); see each file.
inline void report(const char* name, const Args& a, double sec, double work, uint64_t checksum) {
    printf("RESULT name=%s ws_mb=%g iters=%d roi_sec=%.6f work=%.0f checksum=%llu\n", name, a.ws_mb,
           a.iters, sec, work, (unsigned long long)checksum);
}

inline void* alloc64(size_t bytes) {
    bytes = (bytes + 63) & ~size_t(63);
    void* p = aligned_alloc(64, bytes);
    if (!p) {
        fprintf(stderr, "alloc failed\n");
        exit(1);
    }
    return p;
}
