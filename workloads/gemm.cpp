// Blocked single-precision C += A*B, three NxN matrices. work = flops (2N^3/iter).
#include "common.h"
#include <cmath>
int main(int argc, char** argv) {
    Args a = parse_args(argc, argv);
    const size_t BS = 32;
    size_t N = (size_t)std::sqrt((double)a.bytes() / 12.0);
    N = (N / BS) * BS;
    if (N < BS)
        N = BS;
    float *A = (float*)alloc64(N * N * 4), *B = (float*)alloc64(N * N * 4),
          *C = (float*)alloc64(N * N * 4);
    Rng r(a.seed);
    for (size_t i = 0; i < N * N; i++) {
        A[i] = r.unit();
        B[i] = r.unit();
        C[i] = 0;
    }
    const size_t nb = (N + BS - 1) / BS;
    RoiProgress prog((uint64_t)a.iters * nb);
    roi_begin();
    for (int it = 0; it < a.iters; it++)
        for (size_t ii = 0; ii < N; ii += BS) {
            for (size_t kk = 0; kk < N; kk += BS)
                for (size_t jj = 0; jj < N; jj += BS)
                    for (size_t i = ii; i < ii + BS; i++)
                        for (size_t k = kk; k < kk + BS; k++) {
                            float aik = A[i * N + k];
                            for (size_t j = jj; j < jj + BS; j++)
                                C[i * N + j] += aik * B[k * N + j];
                        }
            prog.at((size_t)it * nb + ii / BS + 1);
        }
    double sec = roi_end();
    double s = 0;
    for (size_t i = 0; i < N * N; i += 101)
        s += C[i];
    report("gemm", a, sec, 2.0 * N * N * N * a.iters, (uint64_t)std::fabs(s));
}
