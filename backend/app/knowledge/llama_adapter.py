"""Optional LlamaIndex retriever boundary. No Settings, LLM, agent or index creation."""
import asyncio


def make_retriever(tools):
    # Lazy optional imports: core is not a new backend runtime dependency.
    from llama_index.core.retrievers import BaseRetriever
    from llama_index.core.schema import NodeWithScore,TextNode
    from llama_index.core.callbacks import CallbackManager

    class PermissionAwareRetriever(BaseRetriever):
        async def _aretrieve(self,query_bundle):
            return await asyncio.to_thread(self._retrieve,query_bundle)

        def _retrieve(self,query_bundle):
            result=tools.call('campus_policies',{'query':query_bundle.query_str,'limit':5})
            if result['status']!='SYNTHETIC_EVIDENCE': return []
            return [NodeWithScore(node=TextNode(id_=r['citation']['chunk_id'],text=r['excerpt'],
                metadata={'citation':r['citation'],'untrusted_data':True,'synthetic':True}),score=r['score'])
                for r in result['sources']]
    return PermissionAwareRetriever(callback_manager=CallbackManager([]))
