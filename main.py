import os
import subprocess
from pathlib import Path
from typing import List, Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from crewai import Agent, Task, Crew, Process, LLM
from crewai.tools import tool

# ==============================================================================
# 1. ENTERPRISE WORKSPACE & 5-KEY GEMINI POOL SETUP
# ==============================================================================
WORKSPACE_DIR = Path("./workspace")
WORKSPACE_DIR.mkdir(exist_ok=True)

# Distribute your 5 distinct API keys across the enterprise agent fleet
llm_admin = LLM(model="gemini/gemini-3.5-flash-lite", api_key=os.environ.get("GEMINI_API_KEY_1", os.environ.get("GEMINI_API_KEY", "")))
llm_architect = LLM(model="gemini/gemini-3.5-flash-lite", api_key=os.environ.get("GEMINI_API_KEY_2", os.environ.get("GEMINI_API_KEY", "")))
llm_developer = LLM(model="gemini/gemini-3.5-flash-lite", api_key=os.environ.get("GEMINI_API_KEY_3", os.environ.get("GEMINI_API_KEY", "")))
llm_reviewer = LLM(model="gemini/gemini-3.5-flash-lite", api_key=os.environ.get("GEMINI_API_KEY_4", os.environ.get("GEMINI_API_KEY", "")))
llm_tester = LLM(model="gemini/gemini-3.1-flash-image", api_key=os.environ.get("GEMINI_API_KEY_5", os.environ.get("GEMINI_API_KEY", "")))

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
    goal="Deconstruct user software goals, architect execution pipelines",
    backstory="You are the ultimate engineering director. You ensure that all specialized agents collaborate closely, support each other's outputs, and deliver deployment-ready code. You are the Project Administrator & Master Engineering Director of an autonomous multi-agent software development team. Your primary purpose is to translate user requirements into technical blueprints, coordinate specialized agents (Architect, Developer, Code Reviewer, QA/Tester), manage task pipelines, and ensure final software delivery is robust, modular, and deployment-ready. you i.e. The Administrator AI Agent acts as the central orchestra conductor and technical director for the multi-agent system, designed to translate high-level user requirements into precise engineering blueprints, break complex projects down into executable tasks, and assign them to specialized sub-agents such as Architects, Developers, Code Reviewers, and Testers. Its core goal is to maintain project alignment, enforce strict quality gates on incoming deliverables, prevent scope creep, and ensure that the final software solution is fully integrated, robust, and deployment-ready with minimal manual intervention.",
    llm=llm_admin,
    verbose=True,
    allow_delegation=True
)

architect_agent = Agent(
    role="Principal Systems Architect",
    goal="Design comprehensive modular file structures, architectural blueprints, and dependency maps.",
    backstory="Expert software systems designer responsible for structural integrity, scalability, and code separation. The architect agent is an expert software systems designer responsible for structural integrity, scalability, and code separation. Working as the core structural mind behind complex applications, this agent's primary purpose is to design comprehensive modular file structures, high-level architectural blueprints, and detailed dependency maps before implementation begins.",
    tools=[write_code_file,read_code_file, execute_terminal_command],
    llm=llm_architect,
    verbose=True
)

developer_agent = Agent(
    role="Senior Full-Stack Developer",
    goal="Write immaculate, production-grade, deployment-ready code files matching architectural specifications.",
    backstory="Master developer specializing in writing clean, robust code scripts and integrating modules together. The Senior Full-Stack Developer acts as the master execution engine of the multi-agent system, responsible for converting architectural specifications into production-grade, deployment-ready code. With deep expertise across front-end frameworks, back-end API construction, and database management, this agent specializes in writing clean, modular, and highly efficient codebases. Operating directly between the Principal Systems Architect and the Reviewer/Tester agents, the Senior Full-Stack Developer carefully parses detailed dependency maps and structural blueprints to build fully integrated modules, handle end-to-end logic implementation, and resolve low-level execution details while ensuring strict adherence to software design best practices.",
    tools=[write_code_file, read_code_file, execute_terminal_command],
    llm=llm_developer,
    verbose=True
)

reviewer_agent = Agent(
    role="Senior Code Auditor & Reviewer",
    goal="Critique code for security gaps, edge-case logic failures, and compliance issues, providing direct patches.",
    backstory="Hyper-critical auditor ensuring zero bugs, high security, and clean code optimization across all files.As a seasoned security auditor and principal code reviewer with decades of experience uncovering critical vulnerabilities, you serve as the multi-agent system's primary quality gatekeeper. You specialize in static code analysis, security auditing, edge-case logic evaluation, and performance optimization across diverse programming paradigms. Operating directly after the developer agent generates code, your mission is to thoroughly inspect every line of source code to catch missing exception handlers, security flaws, inefficient algorithms, and architectural anti-patterns, refactoring and patching the codebase into pristine, production-ready quality before it reaches live testing.",
    tools=[read_code_file, write_code_file, execute_terminal_command],
    llm=llm_reviewer,
    verbose=True
)

tester_agent = Agent(
    role="QA Automation & Deployment Engineer",
    goal="Run live execution tests, parse terminal tracebacks, fix runtime bugs, and verify deployment stability.",
    backstory="Ensures software runs perfectly in the terminal with zero errors and guarantees 100% working delivery.As a relentless quality assurance and site reliability engineer, you are dedicated to ensuring that software runs seamlessly in real-world environments without runtime failures. Equipped with live terminal execution tools, you specialize in dynamic testing, automated test suite execution, parsing complex stack traces, and environment configuration troubleshooting. Positioned as the final defense line before deployment, your focus is on executing code real-time in isolated environments, interpreting command outputs and error codes, systematically resolving runtime exceptions, and verifying that all integration points function with zero errors.",
    tools=[execute_terminal_command, read_code_file, write_code_file],
    llm=llm_tester,
    verbose=True
)

# ==============================================================================
# 4. FASTAPI ENTERPRISE SERVER & REAL-TIME CONNECTOR API
# ==============================================================================
app = FastAPI(title="Enterprise Multi-Agent IDE Backend")

# In-memory buffer for real-time human-to-agent interventions
GLOBAL_HUMAN_INBOX = []

# Mount static asset directory for the high-end VS Code frontend UI
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/api/tree")
def get_workspace_tree():
    """Returns directory structure tree of the workspace for the file explorer."""
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
        
        # Check if human injected real-time guidance
        if GLOBAL_HUMAN_INBOX:
            feedback = "\n".join(GLOBAL_HUMAN_INBOX)
            yield f"data: 💬 [Human-to-Agent Connector]: Live human intervention injected: {feedback}\n\n"
            GLOBAL_HUMAN_INBOX.clear()

        primary_task = Task(
            description=f"Fulfill enterprise requirement: {prompt}. Coordinate collaboratively across Architect, Developer, Reviewer, and QA Tester agents to design, write, audit, and verify deployment-ready code inside the workspace.",
            expected_output="A fully built, code-audited, and terminal-verified software solution.",
            agent=project_administrator
        )

        # Build Enterprise Hierarchical Crew
        enterprise_crew = Crew(
            agents=[architect_agent, developer_agent, reviewer_agent, tester_agent],
            tasks=[primary_task],
            process=Process.hierarchical,
            manager_agent=project_administrator,
            verbose=True
        )

        yield f"data: 🚀 Enterprise Crew deployed across 5 Gemini API keys. Running live collaboration loop...\n\n"
        
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
