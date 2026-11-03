// LLM-decode proxy: one token per iter through L layers. Each layer streams its C x C weight
// matrix (matvec), then attends over its own KV cache: scores = softmax(K q), out = V^T scores.
// Working set: ~70% weights, ~30% KV cache. The KV cache starts part-filled (the prompt) and
// gains one row per layer per token; the tail pages are first touched during the ROI, as in a
// real decode. Once full it wraps (sliding window). work = bytes of weights + KV read.
#include "common.h"
#include <algorithm>
#include <cmath>

// 8 independent partial sums so the loop is limited by memory, not by FP-add latency.
static inline float dot(const float* a, const float* b, size_t n) {
    float p[8] = {0, 0, 0, 0, 0, 0, 0, 0};
    size_t i = 0;
    for (; i + 8 <= n; i += 8)
        for (int j = 0; j < 8; j++)
            p[j] += a[i + j] * b[i + j];
    for (; i < n; i++)
        p[0] += a[i] * b[i];
    return ((p[0] + p[1]) + (p[2] + p[3])) + ((p[4] + p[5]) + (p[6] + p[7]));
}

int main(int argc, char** argv) {
    Args a = parse_args(argc, argv);
    const size_t D = 128;            // head dim
    const size_t KV_ROW = 2 * D * 4; // one K row + one V row, bytes
    size_t total = a.bytes();
    size_t wbytes = (size_t)(total * 0.7), kvbytes = total - wbytes;
    size_t C = 1024; // hidden size; shrinks for tiny working sets so there is at least one layer
    while (C > D && C * C * 4 > wbytes)
        C /= 2;
    size_t L = wbytes / (C * C * 4);
    size_t T = L ? kvbytes / L / KV_ROW : 0; // KV capacity per layer, tokens
    if (L < 1 || T < 16) {
        fprintf(stderr, "kvdecode: -s %g MiB too small (need >= ~1 MiB)\n", a.ws_mb);
        return 1;
    }
    size_t T0 = T - std::min(T / 2, (size_t)a.iters); // prompt length already in the cache

    float* W = (float*)alloc64(L * C * C * 4);
    float* K = (float*)alloc64(L * T * D * 4);
    float* V = (float*)alloc64(L * T * D * 4);
    float* x = (float*)alloc64(C * 4);
    float* h = (float*)alloc64(C * 4);
    float* sc = (float*)alloc64(T * 4);
    Rng r(a.seed);
    for (size_t i = 0; i < L * C * C; i++)
        W[i] = (r.unit() - 0.5f) * (2.0f / C);
    for (size_t l = 0; l < L; l++) // prompt: only the first T0 rows of each layer's cache
        for (size_t i = 0; i < T0 * D; i++) {
            K[l * T * D + i] = r.unit() - 0.5f;
            V[l * T * D + i] = r.unit() - 0.5f;
        }
    for (size_t i = 0; i < C; i++)
        x[i] = r.unit() - 0.5f;

    const float inv_sqrt_d = 1.0f / std::sqrt((float)D);
    float out[D];
    double bytes = 0;
    RoiProgress prog((uint64_t)a.iters * L);
    roi_begin();
    for (int tok = 0; tok < a.iters; tok++) {
        size_t pos = (T0 + tok) % T;            // slot for this token's K/V
        size_t len = std::min(T0 + tok + 1, T); // valid rows after appending
        for (size_t l = 0; l < L; l++) {
            const float* Wl = W + l * C * C;
            float* Kl = K + l * T * D;
            float* Vl = V + l * T * D;
            for (size_t i = 0; i < C; i++) // matvec: stream this layer's weights
                h[i] = dot(Wl + i * C, x, C);
            for (size_t j = 0; j < D; j++) { // append this token's K/V (new pages fault here)
                Kl[pos * D + j] = h[j];
                Vl[pos * D + j] = h[D + j < C ? D + j : j];
            }
            float mx = -INFINITY; // scores: scan K, query = first D outputs
            for (size_t t = 0; t < len; t++) {
                sc[t] = dot(Kl + t * D, h, D) * inv_sqrt_d;
                mx = std::max(mx, sc[t]);
            }
            float z = 0;
            for (size_t t = 0; t < len; t++)
                z += (sc[t] = std::exp(sc[t] - mx));
            for (float& o : out)
                o = 0;
            for (size_t t = 0; t < len; t++) { // weighted sum: scan V
                const float* v = Vl + t * D;
                float s = sc[t] / z;
                for (size_t j = 0; j < D; j++)
                    out[j] += s * v[j];
            }
            for (size_t c = 0; c < C; c++) // residual into the next layer's input
                x[c] += 0.1f * std::tanh(h[c]) + 0.1f * out[c % D];
            bytes += (double)C * C * 4 + (double)len * KV_ROW;
            prog.at((size_t)tok * L + l + 1);
        }
    }
    double sec = roi_end();
    float s = 0;
    for (size_t c = 0; c < C; c++)
        s += x[c];
    uint32_t bits;
    memcpy(&bits, &s, 4);
    report("kvdecode", a, sec, bytes, bits);
}
