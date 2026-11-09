"""Explicit per-deployment background installation; definitions contain no secrets."""
import argparse
import json
from pathlib import Path
from gateway.core.deployment import deployment_name

def definitions(config,profiles,*,owner=None):
    code=Path(config['code_root']);configuration=str(Path(config['data_root'])/'deployment.json')
    jobs=[]
    if owner is None:
        jobs.append(dict(name=deployment_name(config),python=config['python'],cwd=str(code),
                         arguments=['-B','-m','gateway','guard','--config',configuration],interval=None))
    for profile in profiles:
        number=profile['owner_id']
        if owner is not None and number!=owner:continue
        jobs.append(dict(name=deployment_name(config,number),python=config['python'],cwd=str(code),
                         arguments=['-B','-m','gateway','worker','--owner',str(number),'--config',configuration],interval=60))
    if owner is not None and not jobs:raise PermissionError('Active owner required')
    return jobs

def start(config):
    from gateway.platforms import current
    home=Path(config['data_root'])/'profile'
    if any((home/name).exists() for name in ('maintenance.request','stop.request')):
        raise RuntimeError('Maintenance or stop hold is active; no task started')
    current().start_background(definitions(config,[])[0])


def main():
    from gateway.core.bootstrap import ROOT,CONFIG
    from gateway.core.members import Members
    from gateway.platforms import current
    parser=argparse.ArgumentParser();parser.add_argument('--install',action='store_true');parser.add_argument('--start',action='store_true');parser.add_argument('--owner',type=int)
    args=parser.parse_args()
    if CONFIG is None:raise RuntimeError('Persistent --config is required for background setup')
    if args.start:
        if args.install or args.owner is not None:raise ValueError('Start cannot combine with install or owner')
        start(CONFIG);print('Scheduled Gateway start requested; inspect fresh health');return
    members=Members(ROOT/'profile/members.sqlite',ROOT)
    try:jobs=definitions(CONFIG,members.active_profiles(),owner=args.owner)
    finally:members.close()
    if args.install:
        current().install_background(jobs)
        (ROOT/'profile/background-installed').write_text(CONFIG['deployment_id'],encoding='ascii')
    print(json.dumps(jobs,ensure_ascii=False))
