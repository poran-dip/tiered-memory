"""Host metadata recorded with every result."""

from tiered_memory import hostinfo


def test_cpu_model_and_memory_from_proc_files(tmp_path):
    (tmp_path / "cpuinfo").write_text("processor\t: 0\nmodel name\t: Test CPU @ 2.60GHz\n")
    (tmp_path / "meminfo").write_text("MemTotal:       16303956 kB\n")
    assert hostinfo.cpu_model(tmp_path / "cpuinfo") == "Test CPU @ 2.60GHz"
    assert hostinfo.mem_total_mib(tmp_path / "meminfo") == 15921


def test_wsl_is_detected_from_the_kernel_release():
    assert hostinfo.is_wsl("6.6.87.2-microsoft-standard-WSL2")
    assert not hostinfo.is_wsl("6.8.0-52-generic")


def test_host_columns_and_git_commit_never_raise(tmp_path):
    cols = hostinfo.host_columns()
    assert set(cols) == {"host_cpu", "host_kernel", "host_wsl", "host_mem_mib"}
    assert hostinfo.git_commit(tmp_path) == ""  # not a checkout
    assert hostinfo.git_commit(hostinfo.REPO).split("-")[0]  # this repo
