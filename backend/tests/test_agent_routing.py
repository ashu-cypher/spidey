import pytest

from app.agents.spidey_agent import SpideyAgent
from app.providers.rule_based import RuleBasedProvider
from app.tools import TOOL_REGISTRY
from app.workflows.engine import WorkflowEngine


@pytest.fixture
def setup():
    provider = RuleBasedProvider()
    agent = SpideyAgent(provider, TOOL_REGISTRY)
    return provider, agent


async def test_calculate_routing(setup):
    provider, agent = setup
    c = await provider.aclassify_intent("calculate 12 * 8")
    assert c["intent"] == "calculate" and "calculator" in c["tools"]
    engine = WorkflowEngine()
    run = engine.create_run("calculate 12 * 8")
    resp = await agent.run("calculate 12 * 8", run, engine)
    assert "96" in resp
    assert engine.get_run(run.workflow_id).status == "completed"


async def test_remember_routing(setup):
    provider, agent = setup
    c = await provider.aclassify_intent("Spidey, remember that I am learning Python")
    assert c["intent"] == "remember" and "memory" in c["tools"]
    engine = WorkflowEngine()
    run = engine.create_run("remember that I am learning Python")
    resp = await agent.run("Spidey, remember that I am learning Python", run, engine)
    assert "Python" in resp


async def test_recall_routing(setup):
    provider, agent = setup
    engine = WorkflowEngine()
    run1 = engine.create_run("remember that I am learning Python")
    await agent.run("Spidey, remember that I am learning Python", run1, engine)
    c = await provider.aclassify_intent("what am I learning?")
    assert c["intent"] == "recall_memory"
    run2 = engine.create_run("what am I learning?")
    resp = await agent.run("what am I learning?", run2, engine)
    assert "Python" in resp


async def test_greeting_no_tools(setup):
    provider, agent = setup
    c = await provider.aclassify_intent("Hello Spidey.")
    assert c["intent"] == "greeting" and c["tools"] == [] and not c["requires_tools"]
    engine = WorkflowEngine()
    run = engine.create_run("Hello Spidey.")
    resp = await agent.run("Hello Spidey.", run, engine)
    # MISSION J.A.R.V.I.S.: the persona now answers as J.A.R.V.I.S., sir.
    assert "sir" in resp.lower()
