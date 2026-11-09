import io,tarfile,tempfile,unittest
from pathlib import Path
import migrate_youtube
class Migration(unittest.TestCase):
 def archive(self,path,unsafe=False):
  with tarfile.open(path,'w:gz') as out:
   for name,body in ([('../escape',b'bad')] if unsafe else [('forma.sqlite3',b'fixture database'),('.forma/server.key',b'fixture key')]):
    item=tarfile.TarInfo(name);item.size=len(body);out.addfile(item,io.BytesIO(body))
 def test_restore_refuses_traversal(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);archive=root/'unsafe.tar.gz';self.archive(archive,True)
   with self.assertRaises(SystemExit):migrate_youtube.restore(archive,root/'output')
   self.assertFalse((root/'escape').exists())
 def test_restore_requires_empty_destination_and_private_key_permissions(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);archive=root/'safe.tar.gz';self.archive(archive);output=root/'output';migrate_youtube.restore(archive,output)
   self.assertEqual((output/'.forma/server.key').stat().st_mode&0o777,0o600)
   with self.assertRaises(SystemExit):migrate_youtube.restore(archive,output)
