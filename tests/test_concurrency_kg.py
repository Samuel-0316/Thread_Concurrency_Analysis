from backend.kg.concurrency_kg import ConcurrencyKG


def test_basic_persist_load(tmp_path):
    p = tmp_path / "kg.json"
    kg = ConcurrencyKG()
    kg.add_finding('f1', {'variable': 'x', 'file': 'a.c'})
    kg.add_node('thread1', type='thread')
    kg.add_thread_relation('thread1', 'f1', 'happens_before')
    kg.persist(str(p))

    loaded = ConcurrencyKG.load(str(p))
    findings = loaded.query_by_type('finding')
    assert 'f1' in findings
