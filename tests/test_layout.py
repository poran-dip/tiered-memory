"""Address map for the full-system configs."""

import pytest

from tiered_memory.layout import IO_HOLE_START, GiB, MiB, format_size, layout, parse_size


def test_parse_and_format_round_trip():
    for text in ["512MiB", "2GiB", "64KiB", "2560MiB"]:
        assert format_size(parse_size(text)) == text
    assert parse_size("512MiB") == 512 * MiB
    assert parse_size("2gib") == 2 * GiB
    assert format_size(2560 * MiB) == "2560MiB"


@pytest.mark.parametrize("bad", ["512MB", "1.5GiB", "", "fast"])
def test_parse_rejects_decimal_and_garbage(bad):
    with pytest.raises(ValueError):
        parse_size(bad)


def test_config_a_is_one_fast_region():
    (r,) = layout("512MiB")
    assert (r.node, r.start, r.size, r.tier) == (0, 0, 512 * MiB, "fast")


def test_config_b_is_contiguous_with_1_to_4_ratio():
    fast, slow = layout("512MiB", "2GiB")
    assert (fast.node, slow.node) == (0, 1)
    assert slow.start == fast.end
    assert slow.size == 4 * fast.size
    assert slow.tier == "slow"


def test_config_c_fits_below_the_io_hole():
    (r,) = layout("2560MiB")
    assert r.end <= IO_HOLE_START


def test_rejects_memory_reaching_the_io_hole():
    with pytest.raises(ValueError, match="I/O hole"):
        layout("1GiB", "2560MiB")


def test_rejects_non_mib_sizes():
    with pytest.raises(ValueError):
        layout("512MiB", "100KiB")
