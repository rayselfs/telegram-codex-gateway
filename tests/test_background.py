import unittest
from pathlib import Path
from gateway.core.background import definitions

class BackgroundTests(unittest.TestCase):
    def test_deployment_and_owner_scoped_jobs_always_carry_configuration(self):
        config={'deployment_id':'a'*32,'data_root':str(Path.cwd()/'資料 space'),'code_root':str(Path.cwd()/'code'),'python':str(Path.cwd()/'python.exe')}
        jobs=definitions(config,[{'owner_id':101},{'owner_id':102}])
        self.assertEqual(len(jobs),3)
        self.assertEqual(len({j['name'] for j in jobs}),3)
        for job in jobs:
            self.assertEqual(job['arguments'][-2:],['--config',str(Path(config['data_root'])/'deployment.json')])
            self.assertTrue(job['name'].startswith('telegram-codex-'+config['deployment_id']))
        self.assertEqual(jobs[1]['arguments'][jobs[1]['arguments'].index('--owner')+1],'101')
        other=definitions({**config,'deployment_id':'b'*32},[{'owner_id':101}])
        self.assertTrue(set(j['name'] for j in jobs).isdisjoint(j['name'] for j in other))
