import os
import sys
import io
import json
import zipfile
import subprocess
from pathlib import Path
from typing import Dict, List, Any, Optional

import uvicorn
from fastapi import FastAPI, Request, File, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse, JSONResponse
from pydantic import BaseModel
from google import genai
from google.genai import types

# ==============================================================================
# 1. CLOUD WORKSPACE & GIT STATE ENGINE
# ==============================================================================
WORKSPACE_DIR = Path("./workspace").resolve()
WORKSPACE_DIR.mkdir(exist_ok=True)

class GitEngine:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self._run(["init"])
        self._run(["config", "user.name", "CloudAgent"])
        self._run(["config", "user.email", "agent@cloud.local"])

    def _run(self, args: List[str]) -> str:
        try:
            res = subprocess.run(["git"] + args, cwd=self.workspace, capture_output=True, text=True)
            return res.stdout.strip()
        except Exception:
            return ""

    def commit(self, message: str):
        self._run(["add", "."])
        self._run(["commit", "-m", message, "--allow-empty"])

    def rollback(self):
        self._run(["reset", "--hard", "HEAD"])
        self._run(["clean", "-fd"])

git_engine = GitEngine(WORKSPACE_DIR)

# ==============================================================================
# 2. NATIVE AGENT TOOLS
# ==============================================================================
def write_file(filepath: str, content: str) -> str:
    try:
        target = (WORKSPACE_DIR / filepath).resolve()
        if not str(target).startswith(str(WORKSPACE_DIR)):
            return "ERROR: Path traversal forbidden."
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"SUCCESS: Wrote file {filepath}"
    except Exception as e:
        return f"ERROR: {str(e)}"

def read_file(filepath: str) -> str:
    try:
        target = (WORKSPACE_DIR / filepath).resolve()
        if not str(target).startswith(str(WORKSPACE_DIR)):
            return "ERROR: Path traversal forbidden."
        if target.exists():
            return target.read_text(encoding="utf-8")
        return f"ERROR: File {filepath} not found."
    except Exception as e:
        return f"ERROR: {str(e)}"

def execute_command(command: str) -> str:
    try:
        res = subprocess.run(command, cwd=WORKSPACE_DIR, shell=True, capture_output=True, text=True, timeout=30)
        return f"EXIT_CODE: {res.returncode}\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
    except subprocess.TimeoutExpired:
        return "ERROR: Command timed out after 30 seconds."
    except Exception as e:
        return f"ERROR: {str(e)}"

TOOL_REGISTRY = {
    "write_file": write_file,
    "read_file": read_file,
    "execute_command": execute_command
}

# ==============================================================================
# 3. STREAMING MULTI-AGENT SWARM
# ==============================================================================
class SwarmAgent:
    def __init__(self, name: str, role: str, key_env: str, tools: List[Any] = None):
        self.name = name
        self.role = role
        self.key_env = key_env
        self.tools = tools or []
        api_key = os.environ.get(key_env, os.environ.get("GEMINI_API_KEY", ""))
        self.client = genai.Client(api_key=api_key) if api_key else None

    def run_stream(self, prompt: str, context_history: Optional[List[str]] = None):
        if not self.client:
            yield f"data: {json.dumps({'agent': self.name, 'type': 'error', 'text': f'API key {self.key_env} missing.'})}\n\n"
            return

        config = types.GenerateContentConfig(
            system_instruction=self.role,
            tools=self.tools if self.tools else None,
            temperature=0.1
        )

        contents = context_history.copy() if context_history else []
        contents.append(prompt)
        
        yield f"data: {json.dumps({'agent': self.name, 'type': 'status', 'text': 'Analyzing task & planning...'})}\n\n"

        try:
            response = self.client.models.generate_content(model="gemini-2.5-flash", contents=contents, config=config)

            if response.function_calls:
                for call in response.function_calls:
                    fn_name = call.name
                    args_dict = {k: v for k, v in call.args.items()} if call.args else {}
                    yield f"data: {json.dumps({'agent': self.name, 'type': 'tool_start', 'text': f'Tool Executing: {fn_name}'})}\n\n"

                    if fn_name in TOOL_REGISTRY:
                        tool_result = TOOL_REGISTRY[fn_name](**args_dict)
                        yield f"data: {json.dumps({'agent': self.name, 'type': 'tool_end', 'text': f'Tool Output: {tool_result[:120]}...'})}\n\n"
                        contents.append(f"Tool {fn_name} returned:\n{tool_result}\nContinue.")

                final_response = self.client.models.generate_content(model="gemini-2.5-flash", contents=contents, config=config)
                text_out = final_response.text or "Task complete."
                yield f"data: {json.dumps({'agent': self.name, 'type': 'message', 'text': text_out})}\n\n"
            else:
                text_out = response.text or "Task processing finished."
                yield f"data: {json.dumps({'agent': self.name, 'type': 'message', 'text': text_out})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'agent': self.name, 'type': 'error', 'text': str(e)})}\n\n"

agents = {
    "manager": SwarmAgent("Manager", "System Architect & Leader. Break tasks down and assign specs.", "GEMINI_API_KEY_1"),
    "architect": SwarmAgent("Architect", "System Designer. Write project file layouts using write_file.", "GEMINI_API_KEY_2", [write_file]),
    "developer": SwarmAgent("Developer", "Fullstack Engineer. Implement full application logic with read_file/write_file.", "GEMINI_API_KEY_3", [write_file, read_file]),
    "tester": SwarmAgent("Tester", "QA & Reliability Engineer. Execute system tests with execute_command. Fix bugs via write_file.", "GEMINI_API_KEY_5", [execute_command, read_file, write_file])
}

# ==============================================================================
# 4. FASTAPI BACKEND & FILE APIS
# ==============================================================================
app = FastAPI(title="DevGen IDE Studio")

class CreateFileRequest(BaseModel):
    filepath: str
    content: str = ""

@app.get("/api/tree")
def api_tree():
    def _build(p: Path):
        items = []
        for i in sorted(p.iterdir()):
            if i.name.startswith('.'):
                continue
            items.append({
                "name": i.name,
                "path": str(i.relative_to(WORKSPACE_DIR)),
                "is_dir": i.is_dir(),
                "children": _build(i) if i.is_dir() else []
            })
        return items
    return _build(WORKSPACE_DIR)

@app.get("/api/file")
def api_read_file(path: str):
    target = (WORKSPACE_DIR / path).resolve()
    if not str(target).startswith(str(WORKSPACE_DIR)) or not target.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return {"content": target.read_text(encoding="utf-8")}

@app.post("/api/file/save")
def api_save_file(req: CreateFileRequest):
    res = write_file(req.filepath, req.content)
    return {"status": res}

@app.post("/api/upload")
async def upload_files(files: List[UploadFile] = File(...)):
    uploaded = []
    for file in files:
        file_path = WORKSPACE_DIR / file.filename
        file_path.parent.mkdir(parents=True, exist_ok=True)
        content = await file.read()
        file_path.write_bytes(content)
        uploaded.append(file.filename)
    return {"status": "success", "files": uploaded}

@app.get("/api/export")
def export_workspace():
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for file in WORKSPACE_DIR.rglob("*"):
            if file.is_file() and not file.name.startswith("."):
                rel_path = file.relative_to(WORKSPACE_DIR)
                zip_file.write(file, rel_path)
    zip_buffer.seek(0)
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=devgen_workspace.zip"}
    )

@app.get("/api/stream")
def stream_chat(message: str, target_agent: Optional[str] = "all"):
    def event_stream():
        git_engine.commit("Pre-swarm snapshot")
        yield f"data: {json.dumps({'agent': 'System', 'type': 'status', 'text': '--- SWARM PIPELINE INITIATED ---'})}\n\n"

        if target_agent in agents:
            for log in agents[target_agent].run_stream(message):
                yield log
        else:
            # 1. Management Phase
            plan_logs = []
            for log in agents["manager"].run_stream(f"Create an architectural execution plan for: {message}"):
                plan_logs.append(log)
                yield log

            # 2. Architect Phase
            for log in agents["architect"].run_stream(f"Create project skeleton for plan: {message}"):
                yield log

            # 3. Developer Phase
            for log in agents["developer"].run_stream(f"Implement logic for request: {message}"):
                yield log

            # 4. Testing Phase
            passed = False
            for attempt in range(1, 4):
                yield f"data: {json.dumps({'agent': 'Tester', 'type': 'status', 'text': f'Verification Loop {attempt}/3 initiated'})}\n\n"
                log_cache = []
                for log in agents["tester"].run_stream("Execute main code via execute_command. Fix errors using write_file if EXIT_CODE != 0."):
                    log_cache.append(log)
                    yield log

                if any("EXIT_CODE: 0" in l for l in log_cache):
                    passed = True
                    break

            if passed:
                yield f"data: {json.dumps({'agent': 'System', 'type': 'status', 'text': '✅ SUCCESS: Swarm code verified successfully.'})}\n\n"
                git_engine.commit("Passed autonomous testing")
            else:
                yield f"data: {json.dumps({'agent': 'System', 'type': 'error', 'text': '❌ FAILURE: Automated verification failed. Rolling back Git repository.'})}\n\n"
                git_engine.rollback()

        yield f"data: {json.dumps({'agent': 'System', 'type': 'done', 'text': '[DONE]'})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")

# ==============================================================================
# 5. PROFESSIONAL DASHBOARD UI
# ==============================================================================
HTML_UI = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>DevGen AI - Studio Workspace</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.39.0/min/vs/loader.min.js"></script>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
    <style>
        body { font-family: 'Inter', system-ui, -apple-system, sans-serif; }
        .tab-active { border-bottom: 2px solid #3b82f6; background-color: #1e293b; color: #f8fafc; }
        .custom-scroll::-webkit-scrollbar { width: 5px; height: 5px; }
        .custom-scroll::-webkit-scrollbar-thumb { background: #334155; border-radius: 4px; }
        .custom-scroll::-webkit-scrollbar-track { background: #0f172a; }
    </style>
</head>
<body class="bg-slate-950 text-slate-100 h-screen flex flex-col overflow-hidden">

    <!-- Top Navigation Header -->
    <header class="h-12 bg-slate-900 border-b border-slate-800 px-4 flex justify-between items-center z-20">
        <div class="flex items-center space-x-6">
            <div class="flex items-center space-x-2">
                <i class="fa-solid fa-code-branch text-blue-500 text-lg"></i>
                <span class="font-bold text-base tracking-wide text-white">DevGen<span class="text-blue-500">AI</span></span>
            </div>
            <nav class="hidden md:flex space-x-4 text-xs font-medium text-slate-400">
                <a href="#" class="text-white hover:text-blue-400">Dashboard</a>
                <a href="#" class="hover:text-blue-400">Projects</a>
                <a href="#" class="hover:text-blue-400">Documentation</a>
                <a href="#" class="hover:text-blue-400">Community</a>
            </nav>
        </div>
        
        <div class="flex items-center space-x-3">
            <div class="relative hidden sm:block">
                <i class="fa-solid fa-magnifying-glass absolute left-3 top-2.5 text-xs text-slate-500"></i>
                <input type="text" placeholder="Search workspace..." class="bg-slate-950 text-xs border border-slate-800 rounded-full pl-8 pr-4 py-1 text-slate-300 focus:outline-none focus:border-blue-500 w-48 transition-all">
            </div>
            <button onclick="exportProject()" title="Export Workspace" class="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-xs border border-slate-700 rounded text-slate-200 flex items-center gap-1.5 transition">
                <i class="fa-solid fa-download"></i> <span class="hidden sm:inline">Export</span>
            </button>
            <div class="h-6 w-6 rounded-full bg-blue-600 flex items-center justify-center text-xs font-bold text-white">
                AI
            </div>
        </div>
    </header>

    <!-- Main Workspace Layout Grid -->
    <div class="flex-1 grid grid-cols-12 overflow-hidden">

        <!-- Column 1: AI Agents Hub -->
        <div class="col-span-12 md:col-span-3 lg:col-span-2 bg-slate-900/50 border-r border-slate-800 flex flex-col overflow-hidden">
            <div class="p-3 border-b border-slate-800 flex justify-between items-center">
                <h2 class="text-xs font-bold uppercase tracking-wider text-slate-400 flex items-center gap-2">
                    <i class="fa-solid fa-robot text-blue-400"></i> AI Agents Hub
                </h2>
                <span class="text-[10px] bg-emerald-500/10 text-emerald-400 px-2 py-0.5 rounded-full border border-emerald-500/20">4 Active</span>
            </div>

            <div class="flex-1 overflow-y-auto p-2 space-y-2 custom-scroll" id="agentHub">
                <!-- Agent: Manager -->
                <div class="bg-slate-900 border border-slate-800 rounded-lg p-3 transition hover:border-slate-700" id="card-manager">
                    <div class="flex justify-between items-center mb-1">
                        <span class="font-semibold text-xs text-white flex items-center gap-1.5">
                            <i class="fa-solid fa-user-tie text-purple-400"></i> Manager
                        </span>
                        <span class="h-2 w-2 rounded-full bg-emerald-400" id="status-dot-manager"></span>
                    </div>
                    <div class="text-[10px] text-slate-400 mb-2">Role: System Architecture & Specs</div>
                    <div class="text-[10px] bg-slate-950 p-1.5 rounded border border-slate-800/80 text-slate-300 truncate" id="task-manager">Status: Idle</div>
                </div>

                <!-- Agent: Architect -->
                <div class="bg-slate-900 border border-slate-800 rounded-lg p-3 transition hover:border-slate-700" id="card-architect">
                    <div class="flex justify-between items-center mb-1">
                        <span class="font-semibold text-xs text-white flex items-center gap-1.5">
                            <i class="fa-solid fa-sitemap text-blue-400"></i> Architect
                        </span>
                        <span class="h-2 w-2 rounded-full bg-emerald-400" id="status-dot-architect"></span>
                    </div>
                    <div class="text-[10px] text-slate-400 mb-2">Role: Infrastructure & Files</div>
                    <div class="text-[10px] bg-slate-950 p-1.5 rounded border border-slate-800/80 text-slate-300 truncate" id="task-architect">Status: Idle</div>
                </div>

                <!-- Agent: Developer -->
                <div class="bg-slate-900 border border-slate-800 rounded-lg p-3 transition hover:border-slate-700" id="card-developer">
                    <div class="flex justify-between items-center mb-1">
                        <span class="font-semibold text-xs text-white flex items-center gap-1.5">
                            <i class="fa-solid fa-code text-emerald-400"></i> Developer
                        </span>
                        <span class="h-2 w-2 rounded-full bg-emerald-400" id="status-dot-developer"></span>
                    </div>
                    <div class="text-[10px] text-slate-400 mb-2">Role: Code Implementation</div>
                    <div class="text-[10px] bg-slate-950 p-1.5 rounded border border-slate-800/80 text-slate-300 truncate" id="task-developer">Status: Idle</div>
                </div>

                <!-- Agent: Tester -->
                <div class="bg-slate-900 border border-slate-800 rounded-lg p-3 transition hover:border-slate-700" id="card-tester">
                    <div class="flex justify-between items-center mb-1">
                        <span class="font-semibold text-xs text-white flex items-center gap-1.5">
                            <i class="fa-solid fa-vial-circle-check text-amber-400"></i> Tester
                        </span>
                        <span class="h-2 w-2 rounded-full bg-emerald-400" id="status-dot-tester"></span>
                    </div>
                    <div class="text-[10px] text-slate-400 mb-2">Role: Code Verification & QA</div>
                    <div class="text-[10px] bg-slate-950 p-1.5 rounded border border-slate-800/80 text-slate-300 truncate" id="task-tester">Status: Idle</div>
                </div>
            </div>
        </div>

        <!-- Column 2: Advanced Code Editor & Terminal Output -->
        <div class="col-span-12 md:col-span-6 lg:col-span-5 flex flex-col border-r border-slate-800 bg-slate-950 overflow-hidden">
            <!-- Tab Headers -->
            <div class="bg-slate-900 border-b border-slate-800 flex items-center justify-between px-2 overflow-x-auto custom-scroll" id="editorTabs">
                <div class="flex items-center space-x-1" id="tabList">
                    <div class="px-3 py-2 text-xs font-medium text-slate-300 border-b-2 border-blue-500 bg-slate-950 flex items-center gap-2 cursor-pointer">
                        <i class="fa-regular fa-file-code text-blue-400"></i> <span id="currentFileName">workspace</span>
                    </div>
                </div>
                <div class="flex items-center space-x-2 text-slate-400 text-xs px-2">
                    <button onclick="saveCurrentFile()" title="Save File" class="hover:text-white p-1"><i class="fa-solid fa-floppy-disk"></i></button>
                </div>
            </div>

            <!-- Monaco Editor Container -->
            <div class="flex-1 w-full relative" id="monacoEditor"></div>

            <!-- Integrated Terminal Output -->
            <div class="h-44 bg-slate-950 border-t border-slate-800 flex flex-col">
                <div class="bg-slate-900 px-3 py-1.5 border-b border-slate-800 flex justify-between items-center">
                    <div class="flex space-x-4 text-[11px] font-semibold text-slate-400">
                        <span class="text-white border-b-2 border-blue-500 pb-0.5">TERMINAL / LOGS</span>
                    </div>
                    <button onclick="clearTerminal()" class="text-[10px] text-slate-500 hover:text-slate-300"><i class="fa-solid fa-trash-can"></i> Clear</button>
                </div>
                <div class="flex-1 p-3 font-mono text-xs text-emerald-400 overflow-y-auto custom-scroll space-y-1" id="terminalLog">
                    <div class="text-slate-500">[DevGen System Ready]: Select files or prompt swarm agents to initiate collaboration.</div>
                </div>
            </div>
        </div>

        <!-- Column 3: Project Explorer & Controls -->
        <div class="col-span-12 md:col-span-3 lg:col-span-2 bg-slate-900/40 border-r border-slate-800 flex flex-col overflow-hidden">
            <div class="p-3 border-b border-slate-800">
                <h2 class="text-xs font-bold uppercase tracking-wider text-slate-400 mb-2">Project Explorer</h2>
                <div class="grid grid-cols-2 gap-1.5">
                    <button onclick="createNewFilePrompt()" class="w-full bg-blue-600 hover:bg-blue-500 text-white py-1.5 rounded text-[11px] font-semibold flex items-center justify-center gap-1 transition">
                        <i class="fa-solid fa-plus"></i> New File
                    </button>
                    <label class="w-full bg-slate-800 hover:bg-slate-700 text-slate-200 py-1.5 rounded text-[11px] font-semibold flex items-center justify-center gap-1 cursor-pointer transition">
                        <i class="fa-solid fa-upload"></i> Upload
                        <input type="file" id="fileUploadInput" multiple class="hidden" onchange="handleFileUpload(event)">
                    </label>
                </div>
            </div>

            <!-- File Directory Tree -->
            <div class="flex-1 p-2 overflow-y-auto custom-scroll text-xs" id="fileTree">
                <div class="text-slate-500 text-center py-4">Loading project structure...</div>
            </div>
        </div>

        <!-- Column 4: AI Collaborator & Agent Chat Workspace -->
        <div class="col-span-12 md:col-span-12 lg:col-span-3 bg-slate-900/60 flex flex-col overflow-hidden">
            <!-- Header -->
            <div class="p-3 border-b border-slate-800 flex justify-between items-center bg-slate-900">
                <div>
                    <h2 class="text-xs font-bold text-white flex items-center gap-2">
                        <i class="fa-solid fa-comments text-blue-400"></i> AI Collaborator
                    </h2>
                    <p class="text-[10px] text-slate-400">Direct multi-agent command prompt</p>
                </div>
                <select id="agentTargetSelect" class="bg-slate-950 border border-slate-800 text-[11px] text-slate-300 rounded px-2 py-1 focus:outline-none">
                    <option value="all">@ All Swarm</option>
                    <option value="manager">@ Manager</option>
                    <option value="architect">@ Architect</option>
                    <option value="developer">@ Developer</option>
                    <option value="tester">@ Tester</option>
                </select>
            </div>

            <!-- Chat Stream Box -->
            <div class="flex-1 p-3 overflow-y-auto custom-scroll space-y-3" id="chatStream">
                <div class="bg-slate-900 border border-slate-800 rounded-lg p-3 text-xs">
                    <div class="flex items-center justify-between mb-1 text-slate-400 text-[10px]">
                        <span class="font-bold text-blue-400">System</span>
                        <span>Ready</span>
                    </div>
                    <div class="text-slate-300 leading-relaxed">
                        Welcome to DevGen Studio. Submit instructions below to execute parallel, multi-agent code development and verification.
                    </div>
                </div>
            </div>

            <!-- Prompt Input Area -->
            <div class="p-3 border-t border-slate-800 bg-slate-900">
                <div class="relative">
                    <textarea id="promptInput" rows="3" placeholder="Describe instructions or tasks for the swarm..." class="w-full bg-slate-950 border border-slate-800 rounded-lg p-2.5 text-xs text-white focus:outline-none focus:border-blue-500 transition-colors resize-none custom-scroll"></textarea>
                    <div class="flex justify-between items-center mt-2">
                        <span class="text-[10px] text-slate-500">Press Shift+Enter for newline</span>
                        <button onclick="deploySwarm()" class="bg-blue-600 hover:bg-blue-500 text-white font-semibold px-4 py-1.5 rounded-lg text-xs flex items-center gap-1.5 shadow-md transition active:scale-95">
                            <i class="fa-solid fa-paper-plane"></i> Run Swarm
                        </button>
                    </div>
                </div>
            </div>
        </div>

    </div>

    <!-- Application JavaScript Logic -->
    <script>
        let editor;
        let activePath = "";

        // Initialize Monaco Editor
        require.config({ paths: { 'vs': 'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.39.0/min' }});
        require(['vs/editor/editor.main'], () => {
            editor = monaco.editor.create(document.getElementById('monacoEditor'), {
                value: '// Select a file from the project explorer to begin editing\n',
                language: 'python',
                theme: 'vs-dark',
                automaticLayout: true,
                minimap: { enabled: false },
                fontSize: 13,
                fontFamily: 'Fira Code, Consolas, monospace'
            });
            loadTree();
        });

        // Workspace Tree Renderer
        async function loadTree() {
            try {
                const res = await fetch('/api/tree');
                const data = await res.json();
                document.getElementById('fileTree').innerHTML = renderTree(data);
            } catch(e) {
                console.error("Failed to load directory tree:", e);
            }
        }

        function renderTree(nodes) {
            if(!nodes || !nodes.length) return '<p class="text-slate-500 text-xs p-2">Workspace empty</p>';
            return '<ul class="space-y-1">' + nodes.map(n => `
                <li>
                    <div class="hover:bg-slate-800/80 p-1.5 rounded flex items-center gap-2 cursor-pointer text-slate-300 transition" onclick="openFile('${n.path}')">
                        <i class="fa-solid ${n.is_dir ? 'fa-folder text-amber-400' : getFileIcon(n.name)} text-xs"></i>
                        <span class="truncate">${n.name}</span>
                    </div>
                    ${n.is_dir && n.children ? `<div class="pl-3 border-l border-slate-800 ml-2">${renderTree(n.children)}</div>` : ''}
                </li>`).join('') + '</ul>';
        }

        function getFileIcon(filename) {
            if(filename.endsWith('.py')) return 'fa-brands fa-python text-blue-400';
            if(filename.endsWith('.js')) return 'fa-brands fa-js text-yellow-400';
            if(filename.endsWith('.html')) return 'fa-brands fa-html5 text-orange-500';
            if(filename.endsWith('.json')) return 'fa-solid fa-code text-emerald-400';
            return 'fa-regular fa-file-code text-slate-400';
        }

        // File Operations
        async function openFile(path) {
            try {
                const res = await fetch(`/api/file?path=${encodeURIComponent(path)}`);
                if (!res.ok) return;
                const data = await res.json();
                
                activePath = path;
                document.getElementById('currentFileName').innerText = path;
                editor.setValue(data.content);

                const ext = path.split('.').pop();
                const langMap = { 'js': 'javascript', 'html': 'html', 'json': 'json', 'py': 'python', 'css': 'css' };
                monaco.editor.setModelLanguage(editor.getModel(), langMap[ext] || 'plaintext');
            } catch(e) {
                console.error("Failed to read file:", e);
            }
        }

        async function saveCurrentFile() {
            if(!activePath) return;
            const content = editor.getValue();
            await fetch('/api/file/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ filepath: activePath, content: content })
            });
            logTerminal(`[Saved]: ${activePath}`);
        }

        async function createNewFilePrompt() {
            const filename = prompt("Enter file name/path (e.g., app/main.py):");
            if (!filename) return;
            await fetch('/api/file/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ filepath: filename, content: "" })
            });
            await loadTree();
            await openFile(filename);
        }

        async function handleFileUpload(event) {
            const files = event.target.files;
            if(!files.length) return;
            
            const formData = new FormData();
            for(let file of files) formData.append('files', file);

            await fetch('/api/upload', { method: 'POST', body: formData });
            logTerminal(`[Upload Completed]: ${files.length} file(s) imported.`);
            loadTree();
        }

        function exportProject() {
            window.location.href = '/api/export';
        }

        // Multi-Agent Chat & Swarm Pipeline Execution
        function deploySwarm() {
            const msgInput = document.getElementById('promptInput');
            const msg = msgInput.value.trim();
            const targetAgent = document.getElementById('agentTargetSelect').value;
            if (!msg) return;

            appendChatMessage("User", msg, "user");
            msgInput.value = "";

            const eventSource = new EventSource(`/api/stream?message=${encodeURIComponent(msg)}&target_agent=${encodeURIComponent(targetAgent)}`);

            eventSource.onmessage = function(event) {
                const data = JSON.parse(event.data);

                if (data.type === "done") {
                    eventSource.close();
                    loadTree();
                    if(activePath) openFile(activePath);
                    return;
                }

                if (data.type === "status") {
                    updateAgentTask(data.agent, data.text);
                    logTerminal(`[${data.agent}]: ${data.text}`);
                } else if (data.type === "tool_start" || data.type === "tool_end") {
                    logTerminal(`[${data.agent} Tool]: ${data.text}`);
                } else if (data.type === "message" || data.type === "error") {
                    appendChatMessage(data.agent, data.text, data.type === "error" ? "error" : "agent");
                }
            };
        }

        function updateAgentTask(agentName, text) {
            const key = agentName.toLowerCase();
            const el = document.getElementById(`task-${key}`);
            if (el) el.innerText = text;
        }

        function appendChatMessage(author, text, role) {
            const chatBox = document.getElementById('chatStream');
            const msgId = 'msg-' + Date.now();
            
            let bgClass = "bg-slate-900 border-slate-800";
            let nameColor = "text-blue-400";

            if (role === "user") {
                bgClass = "bg-slate-800/60 border-slate-700";
                nameColor = "text-emerald-400";
            } else if (role === "error") {
                bgClass = "bg-rose-950/30 border-rose-800/50";
                nameColor = "text-rose-400";
            }

            const html = `
                <div class="${bgClass} border rounded-lg p-3 text-xs relative group transition" id="${msgId}">
                    <div class="flex items-center justify-between mb-1.5 text-[10px]">
                        <span class="font-bold ${nameColor}">${author}</span>
                        <button onclick="copyText('${msgId}')" title="Copy Content" class="opacity-0 group-hover:opacity-100 text-slate-400 hover:text-white transition">
                            <i class="fa-regular fa-copy"></i>
                        </button>
                    </div>
                    <div class="text-slate-200 whitespace-pre-wrap leading-relaxed msg-content">${escapeHtml(text)}</div>
                </div>`;

            chatBox.insertAdjacentHTML('beforeend', html);
            chatBox.scrollTop = chatBox.scrollHeight;
        }

        function copyText(containerId) {
            const el = document.getElementById(containerId);
            if (!el) return;
            const content = el.querySelector('.msg-content').innerText;
            navigator.clipboard.writeText(content);
            logTerminal("[System]: Content copied to clipboard.");
        }

        function logTerminal(text) {
            const term = document.getElementById('terminalLog');
            term.insertAdjacentHTML('beforeend', `<div>${escapeHtml(text)}</div>`);
            term.scrollTop = term.scrollHeight;
        }

        function clearTerminal() {
            document.getElementById('terminalLog').innerHTML = '';
        }

        function escapeHtml(str) {
            return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        }
    </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def get_ui():
    return HTML_UI

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
