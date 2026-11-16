"""ROI caps derived from finished runs (no gem5 needed)."""

import pytest

from tiered_memory import caps


def row(run, sim_sec, insts=1_000_000, completed=True):
    return {
        "run": run,
        "sim_sec": str(sim_sec),
        "insts": str(insts),
        "roi_completed": str(completed),
    }


ROWS = [
    row("c-hotcold-over-448", 3.0, insts=700_000_000),
    row("b_gen3x4-hotcold-over-448", 20.0),
    row("b_gen4x16-cxl-hotcold-over-448", 9.0),
    row("b_gen4x16-cxl-tiering-hotcold-over-448", 4.0),
    row("b-hotcold-fits-64", 99.0),  # another tier: never a reference
    row("b_gen3x4-chase-over-448", 50.0),  # another workload
]


def test_fits_tier_a_and_c_have_no_caps():
    assert caps.cap_options("d", "hotcold", "fits", ROWS) == []
    assert caps.cap_options("a", "hotcold", "over", ROWS) == []
    assert caps.cap_options("c", "hotcold", "over", ROWS) == []


def test_d_cap_is_a_multiple_of_the_longest_b_roi_and_c_instructions():
    opts = caps.cap_options("d", "hotcold", "over", ROWS, caps.CapSpec(d_x_b=5, d_insts_x_c=100))
    assert opts == ["--roi-cap-sec", "100", "--roi-cap-insts", "70000000000"]


def test_b_cap_is_a_multiple_of_c():
    assert caps.cap_options("b", "hotcold", "over", ROWS, caps.CapSpec(b_x_c=20)) == [
        "--roi-cap-sec",
        "60",
    ]


def test_zero_disables_a_cap():
    spec = caps.CapSpec(d_x_b=0, d_insts_x_c=0, b_x_c=0)
    assert caps.cap_options("d", "hotcold", "over", ROWS, spec) == []
    assert caps.cap_options("b", "hotcold", "over", ROWS, spec) == []


def test_unfinished_or_capped_runs_are_never_references():
    rows = [row("c-hotcold-over-448", 3.0), row("b_gen3x4-hotcold-over-448", 999, completed=False)]
    with pytest.raises(caps.MissingReference):
        caps.cap_options("d", "hotcold", "over", rows)


def test_d_waits_for_b_and_c():
    with pytest.raises(caps.MissingReference, match="config B"):
        caps.cap_options("d", "hotcold", "over", [row("c-hotcold-over-448", 3.0)])
    with pytest.raises(caps.MissingReference, match="config C"):
        caps.cap_options("b", "hotcold", "over", [])


@pytest.mark.parametrize(
    "run",
    [
        "b-hotcold-over-448",
        "b_gen4x16-hotcold-over-448",
        "b_gen4x16-cxl-hotcold-over-448",
        "b_gen4x16-tiering-hotcold-over-448",
        "b_gen4x16-cxl-tiering-hotcold-over-448",
    ],
)
def test_every_b_run_name_is_matched(run):
    assert caps.b_rows([row(run, 1.0)], "hotcold", "over")
