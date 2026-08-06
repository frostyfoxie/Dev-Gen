// ==============================================================================
// ENTERPRISE IDE FRONTEND CONTROLLER (APP.JS)
// ==============================================================================

let editor;

// Initialize Monaco Editor (VS Code core engine)
require.config({ paths: { 'vs': 'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.39.0/min' }});
require(['vs/editor/editor.main'], () => {
    editor = monaco.editor.create(document.getElementById('monaco'), { 
        value: '# Welcome to the Enterprise Autonomous Agentic IDE\n# Select a generated file from the left explorer panel to inspect deployment code.', 
        language: 'python', 
        theme: 'vs-dark',
        automaticLayout: true,
        minimap: { enabled: true },
        fontSize: 13,
        scrollBeyondLastLine: false,
        renderWhitespace: "selection"
    });
    
    // Initial workspace scan
    loadTree();
});

// Load Workspace File Tree Explorer
async function loadTree() {
    try {
        const res = await fetch('/api/tree');
        const data = await res.json();
        
        const treeContainer = document.getElementById('tree');
        if (data.length === 0) {
            treeContainer.innerHTML = '<p class="text-gray-500 italic p-2">Workspace is currently empty. Deploy crew to generate code.</p>';
            document.getElementById('fileCount').innerText = '0 files';
            return;
        }

        document.getElementById('fileCount').innerText = `${countFiles(data)} files`;
        treeContainer.innerHTML = renderTreeNodes(data);
    } catch (e) {
        console.error("Error loading workspace tree:", e);
    }
}

// Helper to count total files
function countFiles(nodes) {
    let count = 0;
    for (const node of nodes) {
        if (!node.is_dir) count++;
        if (node.children && node.children.length > 0) {
            count += countFiles(node.children);
        }
    }
    return count;
}

// Recursive HTML renderer for file/folder tree
function renderTreeNodes(nodes) {
    let html = '<ul class="space-y-1 pl-1">';
    for (const node of nodes) {
        if (node.is_dir) {
            html += `
                <li class="text-gray-300 font-mono text-xs py-1">
                    <span class="flex items-center gap-1.5 text-gray-400">📁 <strong>${node.name}</strong></span>
                    <div class="pl-3 border-l border-gray-800 mt-1">
                        ${node.children && node.children.length > 0 ? renderTreeNodes(node.children) : '<span class="text-gray-600 text-[10px]">Empty directory</span>'}
                    </div>
                </li>`;
        } else {
            html += `
                <li class="cursor-pointer hover:bg-[#21262d] text-gray-300 hover:text-white px-2 py-1 rounded text-xs flex items-center gap-2 truncate transition" onclick="openFile('${node.path}')">
                    <span>📄</span> <span class="truncate">${node.name}</span>
                </li>`;
        }
    }
    html += '</ul>';
    return html;
}

// Open and load file content into Monaco Editor
async function openFile(path) {
    try {
        const res = await fetch(`/api/file?path=${encodeURIComponent(path)}`);
        if (!res.ok) throw new Error("File not found");
        const data = await res.json();
        
        editor.setValue(data.content);
        document.getElementById('currentFilename').innerText = path;
        
        // Auto-detect language syntax highlighting for Monaco
        const ext = path.split('.').pop().toLowerCase();
        let lang = 'python';
        if (ext === 'js' || ext === 'ts') lang = 'javascript';
        else if (ext === 'html') lang = 'html';
        else if (ext === 'css') lang = 'css';
        else if (ext === 'json') lang = 'json';
        else if (ext === 'md') lang = 'markdown';
        else if (ext === 'sql') lang = 'sql';
        
        monaco.editor.setModelLanguage(editor.getModel(), lang);
    } catch (e) {
        appendTerminalLog(`[Error]: Could not open file ${path}`);
    }
}

// Send live human feedback into the active agent loop
async function sendHumanInput() {
    const inputField = document.getElementById('humanInput');
    const msg = inputField.value.trim();
    if (!msg) return;

    try {
        await fetch('/api/human-input', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: msg })
        });
        
        inputField.value = "";
        appendTerminalLog(`<span class="text-blue-400">> [Human-to-Agent Bridge Injected]: ${escapeHtml(msg)}</span>`);
    } catch (e) {
        appendTerminalLog(`[Error]: Failed to transmit human feedback.`);
    }
}

// Deploy Autonomous Multi-Agent Crew System via SSE Stream
function startSystem() {
    const promptField = document.getElementById('prompt');
    const prompt = promptField.value.trim();
    if (!prompt) {
        alert("Please enter a project objective prompt for the Administrator.");
        return;
    }

    clearTerminal();
    appendTerminalLog(`<span class="text-purple-400">👑 [Administrator Node]: Initializing enterprise project deployment sequence...</span>`);
    appendTerminalLog(`<span class="text-indigo-400">🌐 Spawning CrewAI Hierarchical Pipeline across 5 dedicated Gemini API keys...</span>`);

    const encodedPrompt = encodeURIComponent(prompt);
    const evtSource = new EventSource(`/api/run-autonomous-system?prompt=${encodedPrompt}`);

    evtSource.onmessage = function(e) {
        if (e.data === "[DONE]") {
            evtSource.close();
            appendTerminalLog(`<span class="text-emerald-400 font-bold">🎉 Execution Stream Closed. Syncing workspace files...</span>`);
            loadTree();
            return;
        }
        appendTerminalLog(escapeHtml(e.data));
    };

    evtSource.onerror = function(err) {
        appendTerminalLog(`<span class="text-red-400">❌ Connection error or execution timeout reached. Check backend logs.</span>`);
        evtSource.close();
    };
}

// Terminal utility helpers
function appendTerminalLog(htmlContent) {
    const term = document.getElementById('terminal');
    const div = document.createElement('div');
    div.innerHTML = htmlContent;
    term.appendChild(div);
    term.scrollTop = term.scrollHeight;
}

function clearTerminal() {
    document.getElementById('terminal').innerHTML = `<div>[System Ready]: Administrator node online. Standing by.</div>`;
}

function escapeHtml(text) {
    return text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
}
