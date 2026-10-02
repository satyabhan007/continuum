import json
import logging
from typing import TypedDict, Annotated, Dict, Any, List
from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage, BaseMessage
import subprocess

logger = logging.getLogger(__name__)

# 1. Define the Global State for the Graph
class OrchestratorState(TypedDict):
    """The state dictionary that LangGraph passes between nodes."""
    harnesses: Dict[str, Any]
    active_harness: str | None
    messages: Annotated[List[BaseMessage], "The conversation/execution logs."]
    last_action: str

# 2. Node Functions (The Agents/Tasks in our workflow)

def initialize_agent_node(state: OrchestratorState) -> OrchestratorState:
    """A node that validates and initializes the agent harnesses."""
    logger.info("LangGraph Node: Initializing agents.")
    return {"last_action": "initialized"}

def sync_tmux_node(state: OrchestratorState) -> OrchestratorState:
    """A node that runs health checks on tmux sessions and updates state."""
    logger.info("LangGraph Node: Syncing TMUX state.")
    harnesses = state.get("harnesses", {})
    stale = []
    for name, data in harnesses.items():
        session_name = data.get('session_name')
        if session_name:
            try:
                output = subprocess.check_output(
                    ["tmux", "ls", "-F", "#{session_name}"],
                    stderr=subprocess.DEVNULL,
                    text=True
                )
                is_alive = any(line.strip() == session_name for line in output.strip().split('\n'))
                if not is_alive:
                    stale.append(name)
            except subprocess.CalledProcessError:
                stale.append(name)
            except FileNotFoundError:
                break

    for name in stale:
        if harnesses[name].get('state') != 'stale':
            harnesses[name]['state'] = 'stale'

    return {"harnesses": harnesses, "last_action": "synced_tmux"}

# 3. Build the StateGraph
workflow = StateGraph(OrchestratorState)

# Add nodes
workflow.add_node("initialize", initialize_agent_node)
workflow.add_node("sync_tmux", sync_tmux_node)

# Set edges (Simple linear flow for the orchestrator sweep)
workflow.set_entry_point("initialize")
workflow.add_edge("initialize", "sync_tmux")
workflow.add_edge("sync_tmux", END)

# Compile the graph
orchestrator_app = workflow.compile()

def run_orchestrator_graph(current_state: dict) -> dict:
    """
    Utility function to run the LangGraph workflow given the current dictionary state.
    Returns the final mutated state.
    """
    initial_state = OrchestratorState(
        harnesses=current_state.get("harnesses", {}),
        active_harness=current_state.get("active_harness"),
        messages=[],
        last_action=""
    )

    # Execute the graph
    final_state = orchestrator_app.invoke(initial_state)

    # Return the data in the format expected by our UI/json
    return {
        "harnesses": final_state.get("harnesses", {}),
        "active_harness": final_state.get("active_harness")
    }
