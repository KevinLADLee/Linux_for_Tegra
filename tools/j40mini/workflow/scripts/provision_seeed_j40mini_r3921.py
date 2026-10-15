#!/usr/bin/env python3
"""Apply the authorized local headless profile; never store a plaintext password."""
import getpass
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys

PROJECT = Path(__file__).resolve().parents[1]
WORK = PROJECT / 'work/seeed-j40mini-r39.2.1'
PROFILE = WORK / 'headless-profile.json'
BSP = WORK / 'Linux_for_Tegra'
ROOT = BSP / 'rootfs'


def configure():
    password = getpass.getpass('Password for the unitree image account: ')
    if not password:
        raise ValueError('Empty password')
    encoded = subprocess.check_output(['openssl', 'passwd', '-6', '-stdin'],
                                      input=password + '\n', text=True).strip()
    PROFILE.touch(mode=0o600, exist_ok=True)
    PROFILE.chmod(0o600)
    PROFILE.write_text(json.dumps({'username': 'unitree', 'hostname': 'unitree',
                                   'password_hash': encoded}) + '\n')
    print('Private headless profile saved; plaintext password was not saved.')


def provision():
    assert os.geteuid() == 0
    profile = json.loads(PROFILE.read_text())
    assert profile['username'] == 'unitree' and profile['hostname'] == 'unitree'
    encoded = profile['password_hash']
    assert encoded.startswith('$6$') and '\n' not in encoded and ':' not in encoded
    if not any(line.startswith('unitree:') for line in (ROOT / 'etc/passwd').read_text().splitlines()):
        temporary = secrets.token_urlsafe(32)
        result = subprocess.run(['bash', 'tools/l4t_create_default_user.sh',
                                 '-u', 'unitree', '-p', temporary, '-n', 'unitree', '--accept-license'],
                                cwd=BSP, text=True, capture_output=True)
        print((result.stdout + result.stderr).replace(temporary, '[temporary password omitted]'))
        result.check_returncode()
    shutil.copy2('/usr/bin/qemu-aarch64-static', ROOT / 'usr/bin/qemu-aarch64-static')
    try:
        subprocess.run(['chroot', str(ROOT), 'chpasswd', '--encrypted'],
                       input='unitree:' + encoded + '\n', text=True, check=True)
    finally:
        (ROOT / 'usr/bin/qemu-aarch64-static').unlink(missing_ok=True)
    source = PROJECT / 'configs/seeed-j40mini'
    for file in source.rglob('*'):
        if not file.is_file():
            continue
        dest = ROOT / file.relative_to(source)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(file, dest)
        dest.chmod(0o600 if dest.suffix == '.nmconnection' else 0o644)
        os.chown(dest, 0, 0)
    zone = ROOT / 'etc/localtime'
    zone.unlink(missing_ok=True)
    zone.symlink_to('/usr/share/zoneinfo/Asia/Shanghai')
    (ROOT / 'etc/timezone').write_text('Asia/Shanghai\n')
    for key in (ROOT / 'etc/ssh').glob('ssh_host_*'):
        if key.is_file():
            key.unlink()
    subprocess.run(['systemctl', '--root=' + str(ROOT), 'enable', 'ssh.service',
                    'NetworkManager.service', 'systemd-timesyncd.service',
                    'unitree-first-boot-ssh.service'], check=True)
    clock = ROOT / 'var/lib/systemd/timesync/clock'
    clock.parent.mkdir(parents=True, exist_ok=True)
    clock.touch()
    # Match the image's service account, without relying on host numeric IDs.
    entry = next(line for line in (ROOT / 'etc/passwd').read_text().splitlines()
                 if line.startswith('systemd-timesync:')).split(':')
    os.chown(clock.parent, int(entry[2]), int(entry[3]))
    os.chown(clock, int(entry[2]), int(entry[3]))
    print('Headless unitree account, SSH, timezone, NTP and wired robot profile applied.')


if __name__ == '__main__':
    if sys.argv[1:] == ['--configure-profile']:
        configure()
    elif not sys.argv[1:]:
        provision()
    else:
        raise SystemExit('Usage: provision_seeed_j40mini_r3921.py [--configure-profile]')
