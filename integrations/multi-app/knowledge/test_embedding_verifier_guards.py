"""Credential-free fixtures for authorization and at-most-once boundaries."""
import ast,importlib.util,json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

HERE=Path(__file__).parent

def load(name):
    spec=importlib.util.spec_from_file_location(name.replace('-','_'),HERE/name)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

class OptimizationGuards(unittest.TestCase):
    def test_host_optimization_is_rejected_before_private_or_network_access(self):
        for name in ['verify-installed-embedding.py','verify-embedding-provider.py']:
            with self.subTest(script=name):
                result=subprocess.run([sys.executable,'-O',str(HERE/name)],capture_output=True,text=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
                self.assertNotEqual(result.returncode,0)
                self.assertIn('without optimization',result.stderr)

    def test_embedded_optimization_is_rejected_before_third_party_imports(self):
        for name in ['verify-installed-embedding.py','verify-embedding-provider.py']:
            with self.subTest(script=name):
                tree=ast.parse((HERE/name).read_text())
                code=next(node.value.value for node in tree.body if isinstance(node,ast.Assign)and any(isinstance(t,ast.Name)and t.id=='CODE'for t in node.targets))
                result=subprocess.run([sys.executable,'-O','-c',code],capture_output=True,text=True,input='',env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
                self.assertNotEqual(result.returncode,0)
                self.assertIn('without optimization',result.stderr)

class InstalledReceiptGuards(unittest.TestCase):
    def setUp(self):
        self.module=load('verify-installed-embedding.py');self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);(self.root/'acceptance').mkdir()
        self.state_patch=patch.object(self.module,'credential_state',return_value={'token':'fixture-provider-credential','fingerprint':'a'*64});self.state_patch.start()
        self.module.ROOT=self.root;self.report=self.root/'acceptance/fixture.json';self.attempt=self.report.with_suffix('.json.attempt.json')
        self.argv=['fixture','encode','--allow-model-call','--allow-remote-provider','--expected-base-url',self.module.REMOTE_BASE,'--expected-model','qwen3.7-text-embedding','--expected-dimension','1024','--report-file',str(self.report)]
        self.proof={'passed':True,'action':'encode','sameModelId':True,'baseUrl':self.module.REMOTE_BASE,'modelName':'qwen3.7-text-embedding','dimension':1024,'requestCount':1,'attemptId':'fixture-attempt','credentialFingerprint':'a'*64}
        self.reservation={'action':'encode','baseUrl':self.module.REMOTE_BASE,'modelName':'qwen3.7-text-embedding','expectedDimension':1024,'attemptId':'fixture-attempt','checkIndex':False,'credentialFingerprint':'a'*64}
    def tearDown(self):self.state_patch.stop();self.temp.cleanup()
    def save(self,path,value):path.write_text(json.dumps(value));path.chmod(0o600)
    def test_matching_success_is_reused_without_private_or_network_access(self):
        self.save(self.report,self.proof);self.save(self.attempt,self.reservation)
        with patch.object(sys,'argv',self.argv),patch.object(self.module.subprocess,'run')as run:
            self.assertEqual(self.module.main(),0);run.assert_not_called()
    def test_success_with_mismatched_attempt_is_rejected(self):
        self.save(self.report,self.proof);self.save(self.attempt,{**self.reservation,'attemptId':'different'})
        with patch.object(sys,'argv',self.argv),patch.object(self.module.subprocess,'run')as run:
            with self.assertRaises(AssertionError):self.module.main()
            run.assert_not_called()
    def test_failed_receipt_never_retries(self):
        self.save(self.report,{**self.proof,'passed':False})
        with patch.object(sys,'argv',self.argv),patch.object(self.module.subprocess,'run')as run:
            with self.assertRaises(AssertionError):self.module.main()
            run.assert_not_called()
    def test_factory_cannot_overwrite_paid_receipt(self):
        self.save(self.report,self.proof)
        arguments=[value for value in self.argv if value not in ['--allow-model-call','--allow-remote-provider']];arguments[1]='factory'
        with patch.object(sys,'argv',arguments),patch.object(self.module.subprocess,'run')as run:
            with self.assertRaises(AssertionError):self.module.main()
            run.assert_not_called()
    def test_cached_encoding_cannot_claim_an_unperformed_index_smoke(self):
        self.save(self.report,self.proof);self.save(self.attempt,{**self.reservation,'checkIndex':True})
        with patch.object(sys,'argv',self.argv+['--check-index']),patch.object(self.module.subprocess,'run')as run:
            with self.assertRaises(AssertionError):self.module.main()
            run.assert_not_called()

if __name__=='__main__':unittest.main()
