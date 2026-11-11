"""Synthetic backup/restore HTTP acceptance, temporary accounts only."""
import http.client
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from api import create_server,API_VERSION
from workspace_store import WorkspaceStore
from workspace_backup import create_backup,verify_backup,restore_backup


def run_acceptance():
    with tempfile.TemporaryDirectory(prefix='workspace-recovery-acceptance-') as directory:
        root=Path(directory);store=WorkspaceStore(root/'source.db')
        admin=store.create_user('Синтетический администратор','admin')
        viewer=store.create_user('Синтетический наблюдатель',actor=admin['user_id'])
        matter=store.create_matter(admin['user_id'],'Синтетическое дело')['matter_id']
        document=store.add_document(admin['user_id'],matter,{'title':'Синтетический документ','document':'Оплата после подписания акта. Учебный пример восстановления.'})['document_id']
        store.grant(admin['user_id'],matter,viewer['user_id'],'viewer')
        archive=root/'snapshot.zip';created=create_backup(store.path,archive)
        verify_backup(archive,created['snapshot_sha256'])
        destination=root/'restored.db';restored=restore_backup(archive,destination,created['snapshot_sha256'])
        def recover(command,user):
            process=subprocess.run([sys.executable,str(ROOT/'scripts/manage_workspace.py'),command,'--db',str(destination),'--user-id',user],capture_output=True,text=True,timeout=15)
            if process.returncode:raise ValueError('credential_recovery_failed')
            return json.loads(process.stdout)['token']
        admin_token=recover('recover-admin-token',admin['user_id'])
        viewer_token=recover('recover-user-token',viewer['user_id'])
        server=create_server('127.0.0.1',0,root/'results',workspace_db=destination)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();checks=0
        def post(path,payload,token,expected=200):
            nonlocal checks
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=10)
            try:
                connection.request('POST',path,json.dumps(payload).encode(),{'Content-Type':'application/json','Authorization':'Bearer '+token})
                response=connection.getresponse();data=json.load(response)
                if response.status!=expected:raise ValueError('unexpected_http_status')
                checks+=1;return data
            finally:connection.close()
        try:
            post('/workspace/me',{},admin['token'],401)
            post('/workspace/me',{},viewer['token'],401)
            post('/workspace/me',{},admin_token)
            assert post('/workspace/documents/read',{'document_id':document},viewer_token)['text'].startswith('Оплата')
            assert post('/workspace/search',{'matter_id':matter,'query':'Оплата'},viewer_token)['matches']
            outsider=post('/workspace/users/create',{'name':'Синтетический посторонний'},admin_token)
            post('/workspace/documents/read',{'document_id':document},outsider['token'],403)
            post('/workspace/documents/add',{'matter_id':matter,'title':'Запрещено','document':'У наблюдателя нет прав записи.'},viewer_token,403)
            post('/workspace/audit',{'matter_id':matter},admin_token)
            assert server.workspace.verify_audit()
            assert store.authenticate(admin['token']) is not None
        finally:
            server.shutdown();server.server_close();thread.join(3)
        return {'status':'passed','api_version':API_VERSION,'http_checks':checks,'scope':'temporary_synthetic_backup_restore_http',
                'old_credentials_rejected':True,'reissued_credentials_work':True,'viewer_and_outsider_acl_preserved':True,
                'search_and_audit_valid':True,'source_database_unchanged':True,'restore_used_new_file':True,
                'tokens_disabled_on_restore':restored['tokens_disabled'],'encrypted':False,
                'credentials_in_report':False,'external_providers_tested':False,'browser_acceptance':False,'legal_acceptance':False}


if __name__=='__main__':
    try:print(json.dumps(run_acceptance(),ensure_ascii=False,indent=2))
    except Exception:
        print(json.dumps({'status':'failed','code':'backup_acceptance_failed','credentials_in_report':False}))
        raise SystemExit(1)
