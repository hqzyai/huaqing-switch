"""Synthetic, credential-free evidence-binding fixtures; no provider requests."""
import datetime,json,os,sqlite3,tempfile,unittest
from pathlib import Path
import embedding_evidence as evidence

class EvidenceBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        for folder in ['state/private','state/data','acceptance']:(self.root/folder).mkdir(parents=True,exist_ok=True)
        self.token=self.root/'state/private/knowledge-embedding-provider-token';self.write(self.token,b'fixture-provider-credential')
        self.original={};future=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(days=1)).isoformat()
        for phase,count in [('batch',16),('query',1)]:
            receipt=self.root/('acceptance/knowledge-embedding-provider-'+phase+'.json')
            proof={'passed':True,'phase':phase,'providerBaseUrl':evidence.BASE,'providerModel':evidence.MODEL,'modelId':evidence.MODEL_ID,'dimension':1024,'vectorCount':count,'requestCount':1,'httpStatuses':[200],'databaseFingerprintsPreserved':True,'checks':{'syntheticBoundary':True},'usage':{'total_tokens':count},'attemptId':'fixture-'+phase}
            attempt={'phase':phase,'providerBaseUrl':evidence.BASE,'providerModel':evidence.MODEL,'modelId':evidence.MODEL_ID,'expectedDimension':1024,'expectedVectorCount':count,'attemptId':proof['attemptId'],'startedAtUtc':future}
            self.write(receipt,json.dumps(proof).encode());self.write(receipt.with_suffix('.json.attempt.json'),json.dumps(attempt).encode())
            self.original[receipt]=receipt.read_bytes();self.original[receipt.with_suffix('.json.attempt.json')]=receipt.with_suffix('.json.attempt.json').read_bytes()
        db=sqlite3.connect(self.root/'state/data/switch.sqlite');db.execute('CREATE TABLE settings (key TEXT PRIMARY KEY,value TEXT)')
        store={'activeId':evidence.DEFAULT,'connections':[{'id':evidence.DEFAULT,'revision':1,'model':'deepseek-v4.1-flash','protocol':'openai-chat','baseUrl':evidence.BASE,'apiKey':'fixture-provider-credential'}]}
        db.execute('INSERT INTO settings VALUES (?,?)',('hq.connections.v1',json.dumps(store)));db.commit();db.close()
    def tearDown(self):self.temp.cleanup()
    def write(self,path,value):path.write_bytes(value);path.chmod(0o600)
    def test_attestation_preserves_paid_bytes_and_reuses_same_evidence(self):
        proof=evidence.attest_existing(self.root)
        self.assertTrue(proof['passed']);self.assertEqual(proof,evidence.attest_existing(self.root))
        for path,value in self.original.items():self.assertEqual(path.read_bytes(),value)
    def test_changed_provider_key_invalidates_old_evidence(self):
        evidence.attest_existing(self.root);self.write(self.token,b'fixture-different-credential')
        with self.assertRaises(RuntimeError):evidence.validate_attestation(self.root)
    def test_paid_evidence_edit_is_detected(self):
        evidence.attest_existing(self.root);path=next(iter(self.original));self.write(path,path.read_bytes()+b' ')
        with self.assertRaises(RuntimeError):evidence.validate_attestation(self.root)
    def test_missing_salt_does_not_regenerate_or_overwrite_attestation(self):
        evidence.attest_existing(self.root);path=self.root/'acceptance/knowledge-embedding-credential-attestation.json';original=path.read_bytes()
        salt=self.root/'state/private/knowledge-embedding-attestation-salt';salt.unlink()
        with self.assertRaises(FileNotFoundError):evidence.attest_existing(self.root)
        self.assertFalse(salt.exists());self.assertEqual(path.read_bytes(),original)
    def test_token_created_after_attempt_cannot_attest_old_calls(self):
        path=self.root/'acceptance/knowledge-embedding-provider-batch.json.attempt.json';attempt=json.loads(path.read_bytes());attempt['startedAtUtc']='2020-01-01T00:00:00+00:00';self.write(path,json.dumps(attempt).encode())
        with self.assertRaises(RuntimeError):evidence.attest_existing(self.root)
    def test_attempt_must_pair_with_paid_receipt(self):
        path=self.root/'acceptance/knowledge-embedding-provider-query.json.attempt.json';attempt=json.loads(path.read_bytes());attempt['attemptId']='mismatched';self.write(path,json.dumps(attempt).encode())
        with self.assertRaises(RuntimeError):evidence.attest_existing(self.root)
    def test_private_token_symlink_is_rejected(self):
        original=self.root/'original-token';self.token.rename(original);self.token.symlink_to(original)
        with self.assertRaises(OSError):evidence.attest_existing(self.root)

if __name__=='__main__':unittest.main()
