#!/usr/bin/env python3
"""Allow only the known Ubuntu-to-NVIDIA OpenCV replacements in this SDK plan."""
from pathlib import Path
import re
import sys

OPENCV_REPLACEMENTS = {
    'libopencv-core-dev', 'libopencv-dnn-dev', 'libopencv-flann-dev',
    'libopencv-imgcodecs-dev', 'libopencv-imgproc-dev', 'libopencv-ml-dev',
    'libopencv-photo-dev', 'libopencv-shape-dev', 'libopencv-video-dev',
    'libopencv-viz-dev',
}
BOOT_CHAIN = re.compile(r'^nvidia-l4t-(core|kernel.*|display-kernel|bootloader.*|initrd|firmware.*|optee|extlinux)$')


def validate(text):
    installs, removals = set(), set()
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 2 or fields[0] not in ('Inst', 'Remv'):
            continue
        package = fields[1].split(':')[0]
        assert not BOOT_CHAIN.fullmatch(package), f'Boot-chain modification refused: {line}'
        if fields[0] == 'Remv':
            assert package in OPENCV_REPLACEMENTS, f'Unexpected removal refused: {line}'
            removals.add(package)
        else:
            installs.add(package)
            if package.startswith('nvidia-l4t-'):
                expected = ('13.2-37290934.0' if package == 'nvidia-l4t-cudadebuggingsupport'
                            else '39.2.1-20260806224157')
                assert '(' + expected + ' ' in line, f'Unexpected BSP package version: {line}'
    if not installs:
        assert not removals and 'already the newest version (7.2.1-b49)' in text, 'No parseable install transaction'
    if removals:
        assert {'libopencv', 'libopencv-dev'} <= installs, 'OpenCV replacement packages missing'
    return sorted(removals)


if __name__ == '__main__':
    assert len(sys.argv) == 2
    removed = validate(Path(sys.argv[1]).read_text())
    print(f'JetPack plan passed: no boot-chain modifications; {len(removed)} known OpenCV replacements.')
