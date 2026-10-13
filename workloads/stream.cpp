// STREAM triad: A = B + 3*C. work = bytes moved (24 B/element/iter).
#include "common.h"
int main(int argc, char** argv) {
    Args a = parse_args(argc, argv);
    size_t n = (a.bytes() / (3 * sizeof(double))) & ~size_t(7);
    double *A = (double*)alloc64(n * 8), *B = (double*)alloc64(n * 8), *C = (double*)alloc64(n * 8);
    Rng r(a.seed);
    for (size_t i = 0; i < n; i++) {
        A[i] = 0;
        B[i] = r.unit();
        C[i] = r.unit();
    }
    // -d 1 sweeps backwards. Same result and checksum, but a different paging story once the arrays
    // exceed RAM: setup fills them front to back, so the front is coldest and gets swapped out.
    // A forward ROI pass starts on that swapped front, and each page it faults in evicts the oldest
    // resident page, which is the one it needs next, so nearly every page cycles through swap. A
    // backward pass starts on the resident end and reaches the swapped front last, evicting pages
    // it has already finished with, so only the overflow is swapped in.
    roi_begin();
    for (int it = 0; it < a.iters; it++) {
        if (a.reverse)
            for (size_t i = n; i-- > 0;)
                A[i] = B[i] + 3.0 * C[i];
        else
            for (size_t i = 0; i < n; i++)
                A[i] = B[i] + 3.0 * C[i];
    }
    double sec = roi_end();
    double s = 0;
    for (size_t i = 0; i < n; i += 97)
        s += A[i];
    report("stream", a, sec, 24.0 * n * a.iters, (uint64_t)(s * 1000));
}
