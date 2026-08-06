import os
import sys
import subprocess
import json
from pathlib import Path
from typing import Dict, List, Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
from google import genai
from google.genai import types

# ==============================================================================
# 1. CLOUD WORKSPACE & GIT STATE ENGINE
# ==============================================================================
WORKSPACE_DIR = Path("./workspace")
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
    target = WORKSPACE_DIR / filepath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"SUCCESS: Wrote file {filepath}"

def read_file(filepath: str) -> str:
    target = WORKSPACE_DIR / filepath
    if target.exists():
        return target.read_text(encoding="utf-8")
    return f"ERROR: File {filepath} not found."

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
        self.tools = tools or []
        api_key = os.environ.get(key_env, os.environ.get("GEMINI_API_KEY", ""))
        self.client = genai.Client(api_key=api_key) if api_key else None

    def run_stream(self, prompt: str):
        if not self.client:
            yield f"data: [{self.name}] Error: API key {key_env} missing.\n\n"
            return

        config = types.GenerateContentConfig(
            system_instruction=self.role,
            tools=self.tools if self.tools else None,
            temperature=0.1
        )

        contents = [prompt]
        yield f"data: [{self.name}] Analyzing task...\n\n"
        
        try:
            response = self.client.models.generate_content(model="gemini-2.5-flash", contents=contents, config=config)
            
            if response.function_calls:
                for call in response.function_calls:
                    fn_name = call.name
                    args_dict = {k: v for k, v in call.args.items()} if call.args else {}
                    yield f"data: [{self.name}] 🛠️ Executing Tool: {fn_name}...\n\n"
                    
                    if fn_name in TOOL_REGISTRY:
                        tool_result = TOOL_REGISTRY[fn_name](**args_dict)
                        yield f"data: [{self.name}] 📋 Tool Result: {tool_result[:100]}...\n\n"
                        contents.append(f"Tool {fn_name} returned:\n{tool_result}\nContinue.")
                
                final_response = self.client.models.generate_content(model="gemini-2.5-flash", contents=contents, config=config)
                yield f"data: [{self.name}] ✅ Task Complete.\n\n"
            else:
                yield f"data: [{self.name}] ✅ Task Complete.\n\n"
        except Exception as e:
            yield f"data: [{self.name}] ❌ Error: {str(e)}\n\n"

agents = {
    "manager": SwarmAgent("Manager", "Plan architecture. Output clear file structures.", "GEMINI_API_KEY_1"),
    "architect": SwarmAgent("Architect", "Create boilerplate code using write_file.", "GEMINI_API_KEY_2", [write_file]),
    "developer": SwarmAgent("Developer", "Implement logic. Use read_file and write_file.", "GEMINI_API_KEY_3", [write_file, read_file]),
    "tester": SwarmAgent("Tester", "Run code using execute_command. If EXIT_CODE != 0, fix the file using write_file and execute again.", "GEMINI_API_KEY_5", [execute_command, read_file, write_file])
}

# ==============================================================================
# 4. FASTAPI BACKEND
# ==============================================================================
app = FastAPI()

class ChatRequest(BaseModel):
    message: str

@app.get("/api/tree")
def api_tree():
    def _build(p: Path):
        return [{"name": i.name, "path": str(i.relative_to(WORKSPACE_DIR)), "is_dir": i.is_dir(), 
                 "children": _build(i) if i.is_dir() else []} for i in sorted(p.iterdir()) if not i.name.startswith('.')]
    return _build(WORKSPACE_DIR)

@app.get("/api/file")
def api_read_file(path: str):
    return {"content": (WORKSPACE_DIR / path).read_text(encoding="utf-8")}

@app.get("/api/stream")
def stream_chat(message: str):
    def event_stream():
        git_engine.commit("Pre-swarm snapshot")
        
        yield "data: --- SWARM PIPELINE INITIATED ---\n\n"
        
        # 1. Plan
        for log in agents["manager"].run_stream(f"Create a plan for: {message}"): yield log
        # 2. Architect
        for log in agents["architect"].run_stream(f"Execute this plan: {message}"): yield log
        # 3. Develop
        for log in agents["developer"].run_stream(f"Implement logic for: {message}"): yield log
        
        # 4. Test Loop
        passed = False
        for attempt in range(1, 4):
            yield f"data: --- [Tester] Verification Attempt {attempt}/3 ---\n\n"
            log_cache = []
            for log in agents["tester"].run_stream("Run the main files via execute_command. If EXIT_CODE != 0, rewrite file and retry."):
                log_cache.append(log)
                yield log
            
            if any("EXIT_CODE: 0" in l for l in log_cache):
                passed = True
                break
                
        if passed:
            yield "data: ✅ SUCCESS: Code executed cleanly.\n\n"
            git_engine.commit("Passed autonomous testing")
        else:
            yield "data: ❌ FAILURE: Tests failed 3 times. Rolling back git state...\n\n"
            git_engine.rollback()
            
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")

# ==============================================================================
# 5. MOBILE-RESPONSIVE UI
# ==============================================================================
HTML_UI = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Cloud Swarm IDE</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.39.0/min/vs/loader.min.js"></script>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
        .pane-scroll { overflow-y: auto; -webkit-overflow-scrolling: touch; }
    </style>
</head>
<body class="bg-gray-950 text-gray-100 h-screen flex flex-col overflow-hidden">
    <!-- Header -->
    <header class="bg-black border-b border-gray-800 p-3 flex justify-between items-center shadow-md z-10">
        <h1 class="font-bold text-lg text-blue-500">DevGen Cloud IDE</h1>
        <button onclick="loadTree()" class="p-2 bg-gray-800 rounded-full hover:bg-gray-700">🔄</button>
    </header>

    <!-- Main Content (Responsive Flex) -->
    <div class="flex-1 flex flex-col md:flex-row overflow-hidden relative">
        
        <!-- Sidebar: Files & Chat -->
        <div class="w-full md:w-80 bg-gray-900 border-r border-gray-800 flex flex-col flex-shrink-0 z-10 transition-all">
            <!-- Chat Input -->
            <div class="p-3 border-b border-gray-800 bg-gray-900">
                <textarea id="prompt" class="w-full bg-black border border-gray-700 p-3 rounded-lg text-sm text-white focus:outline-none focus:border-blue-500 transition-colors" rows="3" placeholder="Instruct the swarm..."></textarea>
                <button onclick="startSwarm()" class="mt-3 w-full bg-blue-600 hover:bg-blue-500 py-3 rounded-lg font-bold text-sm shadow-lg transition-transform active:scale-95">Deploy Swarm</button>
            </div>
            
            <!-- File Tree -->
            <div class="flex-1 p-3 pane-scroll" id="tree">
                <p class="text-gray-500 text-sm">Loading workspace...</p>
            </div>
        </div>

        <!-- Editor & Terminal Area -->
        <div class="flex-1 flex flex-col min-w-0">
            <!-- Editor -->
            <div id="monaco" class="flex-1 bg-black"></div>
            
            <!-- Live Terminal -->
            <div class="h-48 md:h-64 bg-black border-t border-gray-800 p-3 pane-scroll flex flex-col">
                <div class="text-xs font-bold text-gray-500 mb-2 uppercase tracking-wider">Swarm Activity Log</div>
                <div id="terminal" class="font-mono text-[11px] md:text-xs text-emerald-400 space-y-1">System Online. Awaiting instructions...</div>
            </div>
        </div>
    </div>

    <script>
        let editor;
        require.config({ paths: { 'vs': 'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.39.0/min' }});
        require(['vs/editor/editor.main'], () => {
            editor = monaco.editor.create(document.getElementById('monaco'), { 
                value: '# Select a file to view code', 
                language: 'python', 
                theme: 'vs-dark',
                automaticLayout: true,
                minimap: { enabled: false },
                fontSize: 14
            });
            loadTree();
        });

        async function loadTree() {
            const res = await fetch('/api/tree');
            const data = await res.json();
            document.getElementById('tree').innerHTML = renderTree(data);
        }

        function renderTree(nodes) {
            if(!nodes.length) return '<p class="text-gray-500 text-sm">Workspace empty</p>';
            return '<ul class="space-y-1">' + nodes.map(n => `
                <li class="cursor-pointer hover:bg-gray-800 p-2 rounded text-sm flex items-center gap-2 truncate transition-colors" onclick="openFile('${n.path}')">
                    <span class="text-lg">${n.is_dir ? '📁' : '📄'}</span> ${n.name}
                </li>`).join('') + '</ul>';
        }

        async function openFile(path) {
            const res = await fetch(`/api/file?path=${path}`);
            const data = await res.json();
            editor.setValue(data.content);
            const ext = path.split('.').pop();
            const lang = ext === 'js' ? 'javascript' : ext === 'html' ? 'html' : 'python';
            monaco.editor.setModelLanguage(editor.getModel(), lang);
        }

        function startSwarm() {
            const msg = document.getElementById('prompt').value;
            if(!msg) return;
            
            document.getElementById('terminal').innerHTML = `<div>> Executing task: ${msg}</div>`;
            document.getElementById('prompt').value = "";
            
            const eventSource = new EventSource(`/api/stream?message=${encodeURIComponent(msg)}`);
            
            eventSource.onmessage = function(event) {
                if (event.data === "[DONE]") {
                    eventSource.close();
                    loadTree();
                    return;
                }
                const term = document.getElementById('terminal');
                term.innerHTML += `<div>${event.data}</div>`;
                term.scrollTop = term.scrollHeight;
            };
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
