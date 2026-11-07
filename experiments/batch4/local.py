"""Local-only benchmark runner; cannot accept a hosted DB connection or URL."""
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'supabase/tests/local'))
sys.path.insert(0,str(ROOT/'backend'))
import harness

ACTORS={'student':'student2@caldwell.edu','it':'it.staff@caldwell.edu',
        'fac':'fac.staff@caldwell.edu','admin':'sys.admin@caldwell.edu'}


class UserSqlClient:
    """Test-only JWT/RLS session. Never imported by the production backend."""
    def __init__(self,uri,email): self.uri,self.email=uri,email
    def rpc(self,name,params):
        types={
            'kb_search':{'p_query':'text','p_vector':'extensions.vector','p_model':'text','p_revision':'text',
                         'p_kind':'text','p_department':'integer','p_limit':'integer','p_mode':'text'},
            'kb_read_chunks':{'p_ids':'uuid[]'},'kb_departments':{'p_query':'text','p_limit':'integer'},
            'kb_ticket_history':{'p_ticket':'bigint','p_limit':'integer'}}
        parent=self
        class Call:
            def execute(self):
                conn=harness.connect(parent.uri)
                try:
                    harness.act_as(conn,parent.email)
                    conn.execute("set statement_timeout='3s'")
                    args=','.join(f'{k}=>%s::{types[name][k]}' for k in params)
                    row=conn.execute(f'select public.{name}({args}) as r',list(params.values())).fetchone()
                    return type('Result',(),{'data':row['r']})()
                finally: conn.close()
        return Call()


def seed(conn,departments):
    from app.knowledge.core import FixtureDocument,prepare_fixture
    from .benchmark import fixtures
    for source in fixtures()['documents']:
        source=dict(source)
        department=source.pop('department',None)
        if department: source['department_id']=departments[department]
        source['title']='SYNTHETIC FIXTURE: '+source['source_id']
        source['text']='SYNTHETIC FIXTURE: '+source['text']
        meta,chunks=prepare_fixture(FixtureDocument(**source))
        conn.execute('insert into public.kb_documents('+','.join(meta)+') values('+','.join(['%s']*len(meta))+')',list(meta.values()))
        for chunk in chunks:
            chunk=dict(chunk,embedding='['+','.join(map(str,chunk['embedding']))+']')
            casts=['%s::extensions.vector' if k=='embedding' else '%s' for k in chunk]
            conn.execute('insert into public.kb_chunks('+','.join(chunk)+') values('+','.join(casts)+')',list(chunk.values()))


def run_local():
    from app.knowledge.tools import KnowledgeTools
    from .benchmark import evaluate,AuditClient
    if not harness.available(): raise RuntimeError('Optional local PostgreSQL test packages are unavailable')
    # Always create a new local database via embedded pgserver; no environment URL.
    uri=harness.create_database()
    with harness.connect(uri) as owner:
        owner.execute((ROOT/'supabase/optional/batch4_knowledge.sql').read_text())
        departments={r['name']:r['department_id'] for r in owner.execute('select * from public.departments')}
        seed(owner,departments)
        server=owner.execute("select current_setting('server_version') v,(select extversion from pg_extension where extname='vector') e").fetchone()
    def factory(actor,mode,rerank,audit):
        return KnowledgeTools(AuditClient(UserSqlClient(uri,ACTORS[actor]),audit),max_calls=1,
                              search_mode=mode,use_reranker=rerank)
    result=evaluate(factory,departments)
    result.update(database='throwaway-local-postgresql',postgres_version=server['v'],pgvector_version=server['e'])
    return result
