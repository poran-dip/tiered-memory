"""The PCIe link's reference model (tiered_memory/pcie.py), checked by hand against PCIe numbers."""

import pytest

from tiered_memory import pcie


def test_parse_link():
    assert pcie.parse_link("gen4x8") == pcie.LinkSpec(4, 8)
    assert pcie.parse_link(" Gen3X16 ") == pcie.LinkSpec(3, 16)
    assert pcie.parse_link("none") is None
    assert pcie.LinkSpec(5, 16).name == "gen5x16"


@pytest.mark.parametrize("bad", ["gen6x8", "gen0x8", "gen4x3", "gen4", "x8", "pcie4x8", ""])
def test_parse_link_rejects(bad):
    with pytest.raises(ValueError):
        pcie.parse_link(bad)


def test_gen3_x8_rate_matches_neugebauer():
    # 8 lanes x 8 GT/s x 128/130 = 62.96 Gb/s = 7.88 GB/s; less 8% DLL overhead
    raw = pcie.bytes_per_sec(pcie.LinkSpec(3, 8), dll_overhead=0)
    assert raw == pytest.approx(7.877e9, rel=1e-3)
    assert pcie.bytes_per_sec(pcie.LinkSpec(3, 8)) == pytest.approx(raw * 0.92)


def test_gen2_uses_8b10b():
    # 1 lane x 5 GT/s x 8/10 = 4 Gb/s = 500 MB/s
    assert pcie.bytes_per_sec(pcie.LinkSpec(2, 1), dll_overhead=0) == pytest.approx(500e6)


def test_tlp_bytes():
    # MRd: 12 B header + 8 B framing/seq/LCRC; CplD: 12 B header + 8 B + 64 B data
    assert pcie.read_wire_bytes(64) == (20, 84)
    assert pcie.read_wire_bytes(64, addr=5 << 30) == (24, 84)  # 4 DW header above 4 GiB
    assert pcie.write_wire_bytes(64) == 84
    # 512 B read: one MRd (MRRS 512), two CplDs (MPS 256)
    assert pcie.read_wire_bytes(512) == (20, 2 * 20 + 512)


def test_unloaded_read_latency():
    cfg = pcie.link_config("gen4x8", "pcie")
    ser_ns = 104 / pcie.bytes_per_sec(cfg.spec) * 1e9
    assert pcie.unloaded_read_ns(cfg) == pytest.approx(2 * (100 + 30 + 80) + ser_ns)
    assert 420 < pcie.unloaded_read_ns(cfg) < 435
    # the CXL preset lands near CXL-DMSim's +154 ns
    assert 150 < pcie.unloaded_read_ns(pcie.link_config("gen5x16", "cxl")) < 155


def test_bandwidth_limits():
    cfg = pcie.link_config("gen4x8")
    assert pcie.max_read_gbs(cfg) == pytest.approx(pcie.bytes_per_sec(cfg.spec) * 64 / 84 / 1e9)
    # Gen4 x8 tops out just under a DDR3-1600 channel's 12.8 GB/s
    assert 10.5 < pcie.max_read_gbs(cfg) < 11.5
    assert pcie.max_read_gbs(pcie.link_config("gen3x4")) < 3


def test_link_config_overrides_make_it_custom():
    cfg = pcie.link_config("gen4x8", "pcie", rc=50)
    assert cfg.latency == pcie.Latency(50.0, 30.0, 80.0)
    assert cfg.latency_name == "custom"
    assert pcie.link_config("gen4x8", "pcie", rc=100).latency_name == "pcie"
    assert pcie.link_config("none") is None
    with pytest.raises(ValueError):
        pcie.link_config("gen4x8", "fast")
    with pytest.raises(ValueError):
        pcie.link_config("gen4x8", dev=-1)


def test_settings():
    assert pcie.settings(None) == {"link": "none"}
    s = pcie.settings(pcie.link_config("gen3x4", "cxl", tags=32))
    assert s["link"] == "gen3x4"
    assert s["link_latency"] == "cxl"
    assert s["link_tags"] == 32
    assert s["link_expect_rd_ns"] > 150
    assert s["link_expect_rd_gbs"] < 3


def test_variant_tag():
    assert pcie.variant_tag(pcie.link_config("gen4x8")) == ""
    assert pcie.variant_tag(pcie.link_config("gen3x4")) == "gen3x4"
    assert pcie.variant_tag(pcie.link_config("gen4x8", "cxl")) == "gen4x8-cxl"
    assert pcie.variant_tag(pcie.link_config("gen5x16", "cxl")) == "gen5x16-cxl"
    assert pcie.variant_tag(None) == "nolink"
