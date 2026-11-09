import os,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import settings,server,forma_backup,business_store as s,observability,agent_platform

class Backup(unittest.TestCase):
 def test_authenticated_snapshot_preserves_all_agents_and_rejects_tampering(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);database=root/'db.sqlite3';key=root/'server.key';backupkey=root/'backup.key';archive=root/'backup.forma';restore=root/'restored'
   with patch.object(server,'DB',str(database)),patch.object(settings,'KEY_PATH',key),patch('forma_backup.Path.cwd',return_value=root):
    c=sqlite3.connect(database);c.row_factory=sqlite3.Row;settings.init(c);agent_platform.init(c);s.init(c);observability.init(c)
    c.execute('UPDATE managed_agents SET live=1 WHERE id="youtube"');c.execute('CREATE TABLE saved_original_data(value TEXT)');c.execute('INSERT INTO saved_original_data VALUES("preserve-me")');c.commit();c.close()
    # Avoid including the real project's retained private state in this isolated fixture backup.
    prior=os.getcwd();os.chdir(root)
    try:
     forma_backup.backup(archive,backupkey);self.assertEqual(archive.read_bytes()[:4],b'FST1');self.assertEqual(archive.stat().st_mode&0o777,0o600)
     forma_backup.restore(archive,restore,backupkey)
     restored=sqlite3.connect(restore/'database.sqlite3');self.assertEqual(restored.execute('SELECT count(*) FROM managed_agents').fetchone()[0],8);self.assertEqual(restored.execute('SELECT value FROM saved_original_data').fetchone()[0],'preserve-me');restored.close()
     raw=bytearray(archive.read_bytes());raw[25]^=1;archive.write_bytes(raw)
     with self.assertRaises(ValueError):forma_backup.restore(archive,root/'tampered',backupkey)
     self.assertFalse((root/'tampered').exists())
    finally:os.chdir(prior)
