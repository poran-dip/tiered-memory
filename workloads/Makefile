# make native                       -> build/native/*  (optional real-machine calibration)
# make m5                           -> build/m5/*      (static + ROI markers, for gem5 SE mode)
# make m5-fs                        -> build/m5-fs/*   (static + address-mode ROI hypercalls, for
#                                                       gem5 full-system; copied into the disk image)
# gem5 + m5 library first: ../scripts/setup.sh fetch m5  (or the full scripts/setup.sh)
GEM5 ?= $(abspath ../third_party/gem5)
CXX ?= g++
CXXFLAGS = -O2 -std=c++17 -static
WL = stream sort gemm chase kvdecode graph kvstore hashjoin

native: $(addprefix build/native/,$(WL))
build/native/%: %.cpp common.h
	@mkdir -p build/native
	$(CXX) $(CXXFLAGS) $< -o $@

m5: $(addprefix build/m5/,$(WL))
build/m5/%: %.cpp common.h
	@mkdir -p build/m5
	$(CXX) $(CXXFLAGS) -DUSE_M5 -I$(GEM5)/include $< -o $@ -L$(GEM5)/util/m5/build/x86/out -lm5

m5-fs: $(addprefix build/m5-fs/,$(WL))
build/m5-fs/%: %.cpp common.h
	@mkdir -p build/m5-fs
	$(CXX) $(CXXFLAGS) -DUSE_M5_FS -I$(GEM5)/include $< -o $@ -L$(GEM5)/util/m5/build/x86/out -lm5

clean:
	rm -rf build
.PHONY: native m5 m5-fs clean
