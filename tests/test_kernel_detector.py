"""Tests for the dependency-free Linux kernel banner scanner."""

from zero.core.kernel_detector import detect_linux_kernel


def test_detects_banner_across_chunk_boundary(tmp_path):
    image = tmp_path / "linux.raw"
    banner = (
        b"Linux version 5.15.0-91-generic "
        b"(buildd@lcy02-amd64-001) (Ubuntu 11.4.0) #101-Ubuntu SMP\x00"
    )
    # Prefix starts late enough that both the prefix and its terminator cross
    # several deliberately tiny reads.
    image.write_bytes(b"X" * 13 + banner + b"tail" * 512)

    result = detect_linux_kernel(image, chunk_bytes=17)

    assert result.kernel is not None
    assert result.kernel.offset == 13
    assert result.kernel.release == "5.15.0-91-generic"
    assert result.kernel.distro == "ubuntu"
    assert result.kernel.architecture == "x86_64"
    assert result.bytes_scanned == image.stat().st_size


def test_respects_scan_cap_when_banner_is_beyond_it(tmp_path):
    image = tmp_path / "late.raw"
    image.write_bytes(b"X" * 128 + b"Linux version 6.1.0-test (builder) #1 SMP\x00")

    result = detect_linux_kernel(image, max_scan_bytes=96, chunk_bytes=32)

    assert result.kernel is None
    assert result.bytes_scanned == 96
    assert result.limit_reached is True
    assert result.complete is False


def test_rejects_non_printable_false_positive_and_finds_next_banner(tmp_path):
    image = tmp_path / "mixed.raw"
    image.write_bytes(
        b"Linux version 6.1.0-bad\x01not-a-banner\x00"
        b"padding"
        b"Linux version 6.1.0-23-amd64 (debian-kernel@lists.debian.org) "
        b"#1 SMP PREEMPT_DYNAMIC Debian 6.1.99-1\x00"
    )

    result = detect_linux_kernel(image, chunk_bytes=31)

    assert result.kernel is not None
    assert result.kernel.release == "6.1.0-23-amd64"
    assert result.kernel.distro == "debian"


def test_prefers_current_debian_banner_over_stale_ubuntu_string(tmp_path):
    image = tmp_path / "debian.raw"
    old = (
        b"Linux version 5.15.0-101-generic "
        b"(buildd@ubuntu) #111-Ubuntu SMP\x00"
    )
    current = (
        b"Linux version 6.12.96+deb13-amd64 "
        b"(debian-kernel@lists.debian.org) #1 SMP PREEMPT_DYNAMIC\x00"
    )
    image.write_bytes(old + b"cached-data" + current + b"padding" + current)

    result = detect_linux_kernel(image, chunk_bytes=29)

    assert result.kernel is not None
    assert result.kernel.release == "6.12.96+deb13-amd64"
    assert result.kernel.distro == "debian"
    assert result.kernel.architecture == "x86_64"
