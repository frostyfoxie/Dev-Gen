import os
import subprocess
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from crewai import Agent, Task, Crew, Process, LLM
from crewai.tools import tool

# ==============================================================================
# 1. ENTERPRISE WORKSPACE & 5-KEY GEMINI POOL SETUP
# ==============================================================================
WORKSPACE_DIR = Path("./workspace")
WORKSPACE_DIR.mkdir(exist_ok=True)

# Fallback mechanism if individual keys are omitted
MASTER_KEY = os.environ.get("GEMINI_API_KEY", "")

llm_admin = LLM(model="gemini/gemini-3.5-flash", api_key=os.environ.get("GEMINI_API_KEY_1", MASTER_KEY))
llm_architect = LLM(model="gemini/gemini-3.5-flash", api_key=os.environ.get("GEMINI_API_KEY_2", MASTER_KEY))
llm_developer = LLM(model="gemini/gemini-3.5-flash", api_key=os.environ.get("GEMINI_API_KEY_3", MASTER_KEY))
llm_reviewer = LLM(model="gemini/gemini-3.5-flash", api_key=os.environ.get("GEMINI_API_KEY_4", MASTER_KEY))
llm_tester = LLM(model="gemini/gemini-3.5-flash", api_key=os.environ.get("GEMINI_API_KEY_5", MASTER_KEY))

# ==============================================================================
# 2. SANDBOXED AGENT TOOLS (FILE I/O & TERMINAL EXECUTION)
# ==============================================================================
@tool("Write Code File")
def write_code_file(filepath: str, content: str) -> str:
    """Writes or overwrites production code files directly inside the project workspace."""
    target = WORKSPACE_DIR / filepath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"SUCCESS: Written production file {filepath}"

@tool("Read Code File")
def read_code_file(filepath: str) -> str:
    """Reads the content of an existing code file from the workspace."""
    target = WORKSPACE_DIR / filepath
    if target.exists():
        return target.read_text(encoding="utf-8")
    return f"ERROR: File {filepath} not found."

@tool("Execute Terminal Command")
def execute_terminal_command(command: str) -> str:
    """Executes live testing, build verification, or shell commands inside the secure workspace."""
    try:
        res = subprocess.run(command, cwd=WORKSPACE_DIR, shell=True, capture_output=True, text=True, timeout=45)
        return f"EXIT_CODE: {res.returncode}\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
    except Exception as e:
        return f"ERROR: {str(e)}"

# ==============================================================================
# 3. HIGH-END COLLABORATIVE MULTI-AGENT CREW SETUP
# ==============================================================================
project_administrator = Agent(
    role="Project Administrator & Master Coordinator",
    goal="Deconstruct user software goals, architect execution pipelines, delegate tasks dynamically, and manage human-to-agent alignment.",
    backstory="You are the ultimate engineering director. You ensure that all specialized agents collaborate closely, support each other's outputs, and deliver deployment-ready code.",
    llm=llm_admin,
    verbose=True,
    allow_delegation=True
)

architect_agent = Agent(
    role="Principal Systems Architect",
    goal="Design comprehensive modular file structures, architectural blueprints, and dependency maps.",
    backstory="Expert software systems designer responsible for structural integrity, scalability, and code separation.",
    tools=[write_code_file],
    llm=llm_architect,
    verbose=True
)

developer_agent = Agent(
    role="Senior Full-Stack Developer",
    goal="Write immaculate, production-grade, deployment-ready code files matching architectural specifications.",
    backstory="Master developer specializing in writing clean, robust code scripts and integrating modules together.",
    tools=[write_code_file, read_code_file],
    llm=llm_developer,
    verbose=True
)

reviewer_agent = Agent(
    role="Senior Code Auditor & Reviewer",
    goal="Critique code for security gaps, edge-case logic failures, and compliance issues, providing direct patches.",
    backstory="Hyper-critical auditor ensuring zero bugs, high security, and clean code optimization across all files.",
    tools=[read_code_file, write_code_file],
    llm=llm_reviewer,
    verbose=True
)

tester_agent = Agent(
    role="QA Automation & Deployment Engineer",
    goal="Run live execution tests, parse terminal tracebacks, fix runtime bugs, and verify deployment stability.",
    backstory="Ensures software runs perfectly in the terminal with zero errors and guarantees 100% working delivery.",
    tools=[execute_terminal_command, read_code_file, write_code_file],
    llm=llm_tester,
    verbose=True
)

# ==============================================================================
# 4. FASTAPI ENTERPRISE SERVER & FRONTEND LINKING
# ==============================================================================
app = FastAPI(title="Enterprise Multi-Agent IDE Backend")

GLOBAL_HUMAN_INBOX = []

# Serve frontend directory properly
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def serve_index():
    """Serves the main VS Code web interface directly from the backend."""
    return FileResponse("static/index.html")

@app.get("/api/tree")
def get_workspace_tree():
    """Returns directory structure tree of the workspace for the file explorer."""
    if not any(WORKSPACE_DIR.iterdir()):
        return []
    def _walk(p: Path):
        return [{"name": i.name, "path": str(i.relative_to(WORKSPACE_DIR)), "is_dir": i.is_dir(), 
                 "children": _walk(i) if i.is_dir() else []} for i in sorted(p.iterdir()) if not i.name.startswith('.')]
    return _walk(WORKSPACE_DIR)

@app.get("/api/file")
def get_file_content(path: str):
    """Retrieves specific file content to populate the Monaco editor."""
    target = WORKSPACE_DIR / path
    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return {"content": target.read_text(encoding="utf-8")}

@app.post("/api/human-input")
def post_human_input(payload: dict):
    """Real-time connector allowing humans to steer or inject instructions directly into active agents."""
    msg = payload.get("message", "")
    if msg:
        GLOBAL_HUMAN_INBOX.append(msg)
    return {"status": "success", "injected_message": msg}

@app.get("/api/run-autonomous-system")
def run_autonomous_system(prompt: str):
    """Executes the collaborative agentic pipeline and streams events via SSE to the UI terminal."""
    def event_stream():
        yield f"data: 👑 [Administrator]: Received prime objective -> '{prompt}'\n\n"
        
        if GLOBAL_HUMAN_INBOX:
            feedback = "\n".join(GLOBAL_HUMAN_INBOX)
            yield f"data: 💬 [Human-to-Agent Connector]: Live human intervention injected: {feedback}\n\n"
            GLOBAL_HUMAN_INBOX.clear()

        primary_task = Task(
            description=f"Fulfill enterprise requirement: {prompt}. Coordinate collaboratively across Architect, Developer, Reviewer, and QA Tester agents to design, write, audit, and verify deployment-ready code inside the workspace.",
            expected_output="A fully built, code-audited, and terminal-verified software solution.",
            agent=project_administrator
        )

        enterprise_crew = Crew(
            agents=[architect_agent, developer_agent, reviewer_agent, tester_agent],
            tasks=[primary_task],
            process=Process.hierarchical,
            manager_agent=project_administrator,
            verbose=True
        )

        yield f"data: 🚀 Enterprise Crew deployed across Gemini API pool. Running live collaboration loop...\n\n"
        
        try:
            result = enterprise_crew.kickoff()
            yield f"data: ✅ Enterprise Autonomous Cycle Finished Successfully!\n\n"
            yield f"data: Summary Report: {str(result)[:400]}...\n\n"
        except Exception as e:
            yield f"data: ❌ Execution Exception: {str(e)}\n\n"

        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
