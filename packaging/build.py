#!/usr/bin/env python3
"""Build the architecture-independent server DEB; never install/start it here."""
from pathlib import Path
import ast,hashlib,shutil,subprocess,sys
ROOT=Path(__file__).resolve().parents[1]
VERSION='1.0.0~dev1'
CONTROL=f'''Package: wallaby-dashboard
Version: {VERSION}
Section: utils
Priority: optional
Architecture: all
Maintainer: Wallaby Suite <347047153+bluesilent9@users.noreply.github.com>
Depends: python3 (>= 3.11), python3-pypdf, python3-odf, tzdata, adduser, systemd, 7zip
Description: Wallaby Bitcoin Dashboard server
 Bitcoin tools, wallet insights, family workflows and Australian CGT reports.
 Loopback web and Agent interfaces are reached through authorised SSH tunnels.
'''
def main():
    stage=ROOT/'build/package';dist=ROOT/'dist'
    if stage.exists():shutil.rmtree(stage)
    (stage/'DEBIAN').mkdir(parents=True);dist.mkdir(exist_ok=True)
    app=stage/'usr/lib/wallaby-dashboard';app.mkdir(parents=True)
    for source in ROOT.iterdir():
        if source.is_file() and source.suffix in ('.py','.html','.txt','.md','.sh'):
            if source.suffix=='.py':ast.parse(source.read_text(),filename=str(source))
            shutil.copy2(source,app/source.name)
    for name in ('assets','vendor'):shutil.copytree(ROOT/name,app/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    (stage/'DEBIAN/control').write_text(CONTROL)
    for name in ('postinst','prerm','postrm'):
        target=stage/'DEBIAN'/name;shutil.copy2(ROOT/'packaging'/name,target);target.chmod(0o755)
    unit=stage/'usr/lib/systemd/system/wallaby-dashboard.service';unit.parent.mkdir(parents=True);shutil.copy2(ROOT/'packaging/wallaby-dashboard.service',unit)
    for path in app.rglob('*'):
        if path.is_file() and path.suffix in ('.cfg','.ods','.pdf','.csv','.psbt','.sqlite3','.log'):raise ValueError('Refusing to package runtime records: '+str(path))
    package=dist/f'wallaby-dashboard_{VERSION}_all.deb'
    subprocess.run(['dpkg-deb','--root-owner-group','--build',str(stage),str(package)],check=True)
    (dist/'SHA256SUMS.txt').write_text(hashlib.sha256(package.read_bytes()).hexdigest()+'  '+package.name+'\n')
    print(package)
if __name__=='__main__':main()
