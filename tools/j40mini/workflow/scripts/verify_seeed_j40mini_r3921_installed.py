#!/usr/bin/env python3
"""Read-only SSH acceptance of the flashed J40 Mini; write local evidence only."""
import argparse
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

PROJECT = Path(__file__).resolve().parents[1]
WORK = PROJECT / 'work/seeed-j40mini-r39.2.1'
REMOTE = r'''
import hashlib,json,os,re,subprocess
from pathlib import Path

def run(*args):
 p=subprocess.run(args,text=True,capture_output=True)
 return {'code':p.returncode,'stdout':p.stdout.strip(),'stderr':p.stderr.strip()}
def digest(p):
 with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
model=Path('/proc/device-tree/model').read_bytes().decode().strip('\0')
assert 'Jetson' in model, model
release='6.8.12-1021-tegra'
modules={str(p.relative_to('/')):digest(p) for p in Path('/lib/modules',release).rglob('*.ko')}
conf=Path('/etc/nvpmodel.conf')
r={
 'model':model,'compatible':Path('/proc/device-tree/compatible').read_bytes().decode().replace('\0',' ').strip(),
 'l4t':Path('/etc/nv_tegra_release').read_text().splitlines()[0],
 'kernel':run('uname','-r'),'uptime':run('uptime','-p'),'os_release':Path('/etc/os-release').read_text(),
 'power_mode':run('nvpmodel','-q'),'nvpmodel_config':str(conf.resolve()),
 'nvpmodel_sha256':digest(conf),'power_models':re.findall(r'< POWER_MODEL ID=(\d+) NAME=([^ >]+)',conf.read_text()),
 'boot_slots':run('nvbootctrl','dump-slots-info'),
 'bsp_packages':run('dpkg-query','-W','-f=${Package} ${Version} ${db:Status-Status}\n','nvidia-l4t-*'),
 'jetpack':run('dpkg-query','-W','-f=${Version} ${db:Status-Status}','nvidia-jetpack'),
 'dpkg_audit':run('dpkg','--audit'),
 'kernel_image_sha256':digest('/boot/Image'),
 'dtb_sha256':digest('/boot/dtb/tegra234-j40mini-p3768-0000+p3767-0000-recomputer.dtb'),
 'modules':modules,'root_mount':run('findmnt','-n','-o','SOURCE,FSTYPE','/'),
 'wired_addresses':run('ip','-j','-4','address','show','dev','enP8p1s0'),
 'routes':run('ip','-j','-4','route','show'),
 'wired_carrier':Path('/sys/class/net/enP8p1s0/carrier').read_text().strip(),
 'wired_active_connection':run('nmcli','-g','GENERAL.CONNECTION','device','show','enP8p1s0'),
 'wired_profile':{key:run('nmcli','-g',key,'connection','show','unitree-robot') for key in
   ['connection.interface-name','connection.autoconnect','ipv4.method','ipv4.addresses','ipv4.gateway','ipv4.never-default']},
 'ssh_active':run('systemctl','is-active','ssh'),'hostname':run('hostname'),
 'login_user':os.environ.get('SUDO_USER'),'time':run('date','--iso-8601=seconds'),
 'time_sync':run('timedatectl','show','-p','Timezone','-p','NTPSynchronized'),
 'oem_config_pending':Path('/etc/systemd/system/default.target').is_symlink(),
}
print(json.dumps(r))
'''


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='192.168.55.1')
    parser.add_argument('--password-stdin', action='store_true')
    args = parser.parse_args()
    password = sys.stdin.readline().rstrip('\n') if args.password_stdin else getpass.getpass('Jetson unitree password: ')
    assert password and '\n' not in password
    result_path = PROJECT / 'analysis/seeed-j40mini-r39.2.1-flash-result.json'
    flash = json.loads(result_path.read_text())
    assert flash['l4t'] == '39.2.1' and flash['physically_flashed'] and flash['vendor_flash_exit_code'] == 0
    run = Path(flash['log']).parent
    assert run.parent == WORK / 'operations'
    known_hosts = run / 'installed-known-hosts'
    read_fd, write_fd = os.pipe()
    os.write(write_fd, (password + '\n').encode())
    os.close(write_fd)
    try:
        command = ['sshpass', '-d', str(read_fd), 'ssh', '-T', '-o', 'StrictHostKeyChecking=accept-new',
                   '-o', 'UserKnownHostsFile=' + str(known_hosts), '-o', 'ConnectTimeout=10',
                   '-o', 'PubkeyAuthentication=no', '-o', 'PreferredAuthentications=password',
                   '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=3',
                   'unitree@' + args.host, 'sudo -S -p ' + shlex.quote('') + ' python3 -c ' + shlex.quote(REMOTE)]
        process = subprocess.run(command, input=password + '\n', text=True, capture_output=True,
                                 pass_fds=(read_fd,), timeout=180)
    finally:
        os.close(read_fd)
    if process.returncode:
        raise RuntimeError((process.stderr + process.stdout).replace(password, '[redacted]'))
    actual = json.loads(process.stdout)
    build = json.loads((PROJECT / 'analysis/seeed-j40mini-r39.2.1-build.json').read_text())
    root = WORK / 'artifacts/rootfs'
    modules = {str(p.relative_to(root)): digest(p) for p in (root / 'lib/modules/6.8.12-1021-tegra').rglob('*.ko')}
    profile = {k: v['stdout'] for k, v in actual['wired_profile'].items()}
    addresses = json.loads(actual['wired_addresses']['stdout'])
    routes = json.loads(actual['routes']['stdout'])
    packages = [line.split() for line in actual['bsp_packages']['stdout'].splitlines()]
    installed = {p[0]: p[1] for p in packages if len(p) == 3 and p[2] == 'installed'}
    expected_bsp = '39.2.1-20260806224157'
    super_config = WORK / 'Linux_for_Tegra/rootfs/etc/nvpmodel/nvpmodel_p3767_0000_super.conf'
    checks = {
        'l4t_r39_2_1': '# R39 (release), REVISION: 2.1,' in actual['l4t'],
        'bsp_packages_consistent': installed.get('nvidia-l4t-core') == expected_bsp
            and installed.get('nvidia-l4t-bootloader') == expected_bsp
            and all(p[2] in ('installed','not-installed','config-files') for p in packages if len(p) == 3)
            and all('39.2.0-' not in v for v in installed.values()),
        'qspi_version_r39_2_1': actual['boot_slots']['code'] == 0 and 'Current version: 39.2.1' in actual['boot_slots']['stdout'],
        'kernel_release': actual['kernel']['stdout'] == build['kernel_release'],
        'kernel_image_matches': actual['kernel_image_sha256'] == build['kernel_image_sha256'],
        'dtb_matches': actual['dtb_sha256'] == build['target_dtb_sha256'],
        'all_compiled_modules_match': actual['modules'] == modules,
        'super_device_tree': 'nvidia,p3768-0000+p3767-0000-super' in actual['compatible'],
        'super_power_config': actual['nvpmodel_sha256'] == digest(super_config)
            and ['0','MAXN_SUPER'] in actual['power_models'] and ['4','40W'] in actual['power_models'],
        'default_super_40w_mode': actual['power_mode']['stdout'].splitlines() == ['NV Power Mode: 40W','4'],
        'wired_ipv4_active': any(a.get('local') == '192.168.123.18' and a.get('prefixlen') == 24
            for interface in addresses for a in interface.get('addr_info', [])),
        'wired_persistent_profile': profile.get('connection.interface-name') == 'enP8p1s0'
            and profile.get('connection.autoconnect') == 'yes' and profile.get('ipv4.method') == 'manual'
            and profile.get('ipv4.addresses') == '192.168.123.18/24' and profile.get('ipv4.never-default') == 'yes'
            and profile.get('ipv4.gateway') in ('','--'),
        'wired_no_default_route': not any(r.get('dst') == 'default' and r.get('dev') == 'enP8p1s0' for r in routes),
        'root_on_nvme': actual['root_mount']['stdout'].startswith('/dev/nvme0n1p1 '),
        'headless_login': actual['login_user'] == 'unitree' and actual['hostname']['stdout'] == 'unitree'
            and actual['ssh_active']['stdout'] == 'active' and not actual['oem_config_pending'],
        'dpkg_clean': actual['dpkg_audit']['code'] == 0 and not actual['dpkg_audit']['stdout'],
        'jetpack_sdk_not_preinstalled': actual['jetpack']['code'] != 0,
    }
    actual.pop('modules')
    report = {'verified_at': datetime.now(timezone.utc).isoformat(), 'l4t': '39.2.1',
              'board': 'recomputer-orin-j40mini', 'host': args.host, 'checks': checks,
              'requested_checks_passed': all(checks.values()), 'compiled_module_count': len(modules),
              'actual': actual, 'full_peripheral_acceptance_performed': False}
    out = PROJECT / 'analysis/seeed-j40mini-r39.2.1-installed-system.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    (run / 'installed-system.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k:v for k,v in report.items() if k != 'actual'}, ensure_ascii=False, indent=2))
    if not all(checks.values()):
        raise SystemExit('Failed checks: ' + ', '.join(k for k,v in checks.items() if not v))
    flash['installed_system_verified'] = True
    flash['verification_report'] = str(out.relative_to(PROJECT))
    flash['power_mode'] = actual['power_mode']['stdout']
    flash['wired_ipv4'] = '192.168.123.18/24'
    result_path.write_text(json.dumps(flash, ensure_ascii=False, indent=2) + '\n')
    (run / 'flash-result.json').write_text(json.dumps(flash, ensure_ascii=False, indent=2) + '\n')
    print('Installed R39.2.1, compiled kernel/modules, Super 40W and robot wired profile verified.')


if __name__ == '__main__':
    main()
