from app.workflows.engine import WorkflowEngine


def test_run_lifecycle():
    e = WorkflowEngine()
    run = e.create_run("hello")
    assert run.status == "running" and run.started_at
    s = e.add_step(run.workflow_id, "Understand request", "understand")
    assert s.status == "WAITING"
    e.update_step(run.workflow_id, s.step_id, status="RUNNING")
    assert e.get_run(run.workflow_id).steps[0].started_at
    e.update_step(
        run.workflow_id, s.step_id, status="COMPLETED", output={"intent": "greeting"}
    )
    st = e.get_run(run.workflow_id).steps[0]
    assert (
        st.status == "COMPLETED"
        and st.completed_at
        and st.output["intent"] == "greeting"
    )
    e.finish_run(run.workflow_id, "completed", result="hi")
    r = e.get_run(run.workflow_id)
    assert r.status == "completed" and r.completed_at and r.result == "hi"


def test_failed_step():
    e = WorkflowEngine()
    run = e.create_run("x")
    s = e.add_step(run.workflow_id, "Boom", "tool")
    e.update_step(run.workflow_id, s.step_id, status="FAILED", error="nope")
    assert e.get_run(run.workflow_id).steps[0].status == "FAILED"


def test_list_newest_first():
    e = WorkflowEngine()
    a = e.create_run("first")
    b = e.create_run("second")
    ids = [r.workflow_id for r in e.list_runs()]
    assert ids[0] == b.workflow_id


def test_missing_run():
    e = WorkflowEngine()
    assert e.get_run("nope") is None
