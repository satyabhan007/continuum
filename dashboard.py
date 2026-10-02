import json
from flask import Flask, jsonify, request, render_template_string
from multi_harness_orchestrator import MultiHarnessOrchestrator

app = Flask(__name__)
orchestrator = MultiHarnessOrchestrator()

# --- MCP API Endpoints ---

@app.route('/api/state', methods=['GET'])
def get_state():
    """Returns the current state of all harnesses."""
    # Force reload from disk in case external CLI commands modified it
    orchestrator.load_state()
    return jsonify(orchestrator.get_state())

@app.route('/api/health', methods=['POST'])
def trigger_health_check():
    """Triggers a health check on all running harnesses."""
    orchestrator.load_state()
    stale = orchestrator.health_check()
    return jsonify({"status": "success", "stale_found": stale, "state": orchestrator.get_state()})

@app.route('/api/stop', methods=['POST'])
def stop_harness():
    """Stops a specific harness."""
    data = request.json
    name = data.get("name")
    if not name:
        return jsonify({"error": "Missing harness name"}), 400

    orchestrator.load_state()
    success = orchestrator.stop(name)
    if success:
        return jsonify({"status": "success", "message": f"Stopped {name}"})
    return jsonify({"error": "Failed to stop harness"}), 500

@app.route('/api/start', methods=['POST'])
def start_harness():
    """Starts a new harness."""
    data = request.json
    name = data.get("name")
    agent_type = data.get("agent_type", "hermes")
    model_name = data.get("model_name", "default")

    if not name:
        return jsonify({"error": "Missing harness name"}), 400

    orchestrator.load_state()
    success = orchestrator.start(name, agent_type=agent_type, model_name=model_name)
    if success:
        return jsonify({"status": "success", "message": f"Started {name} as {agent_type} using {model_name}"})
    return jsonify({"error": "Failed to start harness or already exists"}), 500

# --- Frontend Visual Dashboard ---

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Continuum AI Agents</title>
    <style>
        /* Minimalist, OpenAI-inspired aesthetic */
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&display=swap');

        body {
            font-family: 'Inter', sans-serif;
            background-color: #0F0F0F; /* Sleek dark background */
            margin: 0;
            padding: 40px 20px;
            color: #ECECEC;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            align-items: center;
        }

        h1 {
            color: #FFFFFF;
            font-weight: 500;
            font-size: 1.8rem;
            letter-spacing: -0.02em;
            margin-bottom: 50px;
            opacity: 0.9;
        }

        .controls { display: flex; justify-content: center; gap: 12px; margin-bottom: 30px; position: relative; z-index: 10; width: 100%; max-width: 800px; }

        button {
            padding: 10px 18px;
            background: rgba(255, 255, 255, 0.08);
            color: #ECECEC;
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 8px;
            cursor: pointer;
            font-family: 'Inter', sans-serif;
            font-size: 13px;
            font-weight: 500;
            transition: all 0.2s ease;
            backdrop-filter: blur(10px);
        }
        button:hover {
            background: rgba(255, 255, 255, 0.15);
            border-color: rgba(255, 255, 255, 0.2);
            transform: scale(1.02);
        }
        .btn-health { color: #10a37f; border-color: rgba(16, 163, 127, 0.3); } /* OpenAI Green */
        .btn-health:hover { background: rgba(16, 163, 127, 0.1); border-color: rgba(16, 163, 127, 0.5); }

        .btn-danger { color: #f44336; border-color: rgba(244, 67, 54, 0.3); background: transparent; }
        .btn-danger:hover { background: rgba(244, 67, 54, 0.1); border-color: rgba(244, 67, 54, 0.5); }

        .grid {
            display: flex;
            flex-wrap: wrap;
            justify-content: center;
            gap: 60px;
            max-width: 1200px;
            margin: 0 auto;
            padding: 40px 0;
        }

        /* The "OpenAI Dot" Agent Representation */
        .card {
            display: flex;
            flex-direction: column;
            align-items: center;
            width: 200px;
            position: relative;
        }

        .dot-container {
            position: relative;
            width: 120px;
            height: 120px;
            display: flex;
            justify-content: center;
            align-items: center;
            margin-bottom: 25px;
        }

        /* The core dot */
        .dot {
            width: 40px;
            height: 40px;
            border-radius: 50%;
            background: #fff;
            position: absolute;
            z-index: 2;
            transition: all 0.5s ease-in-out;
            box-shadow: 0 0 20px rgba(255,255,255,0.2);
        }

        /* The breathing aura (morphing circles behind the dot) */
        .aura {
            position: absolute;
            width: 100%;
            height: 100%;
            border-radius: 40% 60% 70% 30% / 40% 50% 60% 50%;
            background: rgba(255, 255, 255, 0.05);
            animation: morph 8s ease-in-out infinite;
            z-index: 1;
            filter: blur(8px);
        }
        .aura:nth-child(2) { animation-direction: reverse; animation-duration: 10s; background: rgba(255, 255, 255, 0.03); }

        @keyframes morph {
            0%, 100% { border-radius: 40% 60% 70% 30% / 40% 40% 60% 50%; transform: scale(1) rotate(0deg); }
            34% { border-radius: 70% 30% 50% 50% / 30% 30% 70% 70%; transform: scale(1.05) rotate(120deg); }
            67% { border-radius: 100% 60% 60% 100% / 100% 100% 60% 60%; transform: scale(0.95) rotate(240deg); }
        }

        /* State specific pulsing */
        .state-running .dot { animation: breathe 3s ease-in-out infinite alternate; background: #fff; }
        .state-running .aura { background: rgba(255,255,255,0.08); }

        .state-stale .dot { background: #f44336; box-shadow: 0 0 15px rgba(244,67,54,0.5); opacity: 0.5; animation: none; transform: scale(0.8); }
        .state-stale .aura { animation: none; background: rgba(244,67,54,0.05); border-radius: 50%; }

        .state-context_exceeded .dot { background: #ff9800; animation: jitter 0.5s ease-in-out infinite; }
        .state-context_exceeded .aura { background: rgba(255,152,0,0.1); animation-duration: 2s; }

        @keyframes breathe { 0% { transform: scale(0.9); box-shadow: 0 0 10px rgba(255,255,255,0.1); } 100% { transform: scale(1.1); box-shadow: 0 0 25px rgba(255,255,255,0.4); } }
        @keyframes jitter { 0%, 100% { transform: translate(0,0) scale(1); } 25% { transform: translate(1px, -1px) scale(1.02); } 50% { transform: translate(-1px, 1px) scale(0.98); } 75% { transform: translate(1px, 1px) scale(1.01); } }

        /* Typography and Details */
        .card h3 {
            margin: 0 0 8px 0;
            color: #fff;
            font-weight: 500;
            font-size: 1.1rem;
            text-align: center;
        }

        .badge {
            font-size: 10px;
            padding: 3px 8px;
            border-radius: 12px;
            background: rgba(255,255,255,0.1);
            color: #bbb;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            font-weight: 600;
        }

        /* Minimalist Progress Bar */
        .progress-bg {
            background: rgba(255,255,255,0.1);
            border-radius: 4px;
            height: 4px;
            width: 100%;
            margin-top: 8px;
            overflow: hidden;
        }
        .progress-fill { background: #fff; height: 100%; width: 0%; transition: width 0.4s ease; opacity: 0.8; }
        .progress-warning { background: #ff9800; opacity: 1; }
        .progress-danger { background: #f44336; opacity: 1; }

        .details { width: 100%; text-align: center; }
        .details p { margin: 6px 0; font-size: 12px; color: #888; }

        /* Input Forms */
        .form-group { display: flex; gap: 12px; margin-bottom: 50px; justify-content: center; width: 100%; max-width: 800px; }
        select, input {
            padding: 12px 16px;
            background: rgba(255,255,255,0.05);
            color: #fff;
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 8px;
            font-family: 'Inter', sans-serif;
            font-size: 14px;
            transition: all 0.2s ease;
        }
        select:focus, input:focus { outline: none; border-color: rgba(255,255,255,0.3); background: rgba(255,255,255,0.08); }

        /* Clean tooltip for extra info instead of cluttered text */
        .info-hover { cursor: help; color: #666; border-bottom: 1px dotted #666; }
    </style>
</head>
<body>
    <h1>Continuum AI Agents</h1>

    <div class="controls">
        <button onclick="fetchState()">Refresh State</button>
        <button class="btn-health" onclick="runHealthCheck()">Sync LangGraph Nodes</button>
    </div>

    <div class="form-group">
        <input type="text" id="newName" placeholder="Node ID" />
        <select id="newType">
            <option value="hermes">Agent: Hermes</option>
            <option value="jcode">Agent: JCode</option>
            <option value="opencode">Agent: OpenCode</option>
            <option value="antigravity">Agent: Antigravity</option>
        </select>
        <select id="newModel">
            <option value="gpt-4o">Model: GPT-4o (128k)</option>
            <option value="claude-3-5-sonnet">Model: Claude 3.5 Sonnet (200k)</option>
            <option value="gemini-1.5-pro">Model: Gemini 1.5 Pro (2M)</option>
            <option value="gemini-1.5-flash">Model: Gemini 1.5 Flash (1M)</option>
            <option value="llama-3-70b">Model: Llama 3 70B (128k)</option>
        </select>
        <button onclick="startHarness()" style="background: #fff; color: #000; border: none; font-weight: 600;">Initialize Node</button>
    </div>

    <div class="grid" id="harnessGrid">
        <!-- Agent Dots will be injected here via JS -->
    </div>

    <script>
        async function fetchState() {
            const res = await fetch('/api/state');
            const data = await res.json();
            renderGrid(data);
        }

        async function runHealthCheck() {
            const res = await fetch('/api/health', { method: 'POST' });
            const data = await res.json();
            if (data.stale_found.length > 0) alert('LangGraph Sync Warning: Lost contact with nodes: ' + data.stale_found.join(', '));
            renderGrid(data.state);
        }

        async function stopHarness(name) {
            if(!confirm(`Are you sure you want to sever the ${name} node from the LangGraph?`)) return;
            await fetch('/api/stop', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ name })
            });
            fetchState();
        }

        async function startHarness() {
            const name = document.getElementById('newName').value;
            const type = document.getElementById('newType').value;
            const model = document.getElementById('newModel').value;
            if (!name) return alert("Please provide a name.");

            await fetch('/api/start', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ name, agent_type: type, model_name: model })
            });
            document.getElementById('newName').value = "";
            fetchState();
        }

        function renderGrid(state) {
            const grid = document.getElementById('harnessGrid');
            grid.innerHTML = '';

            const harnesses = state.harnesses || {};
            const active = state.active_harness;

            if (Object.keys(harnesses).length === 0) {
                grid.innerHTML = '<p style="grid-column: 1 / -1; text-align: center; color: #aaa;">Sensors detect no active galaxies.</p>';
                return;
            }

            for (const [name, data] of Object.entries(harnesses)) {
                const isRunning = data.state === 'running';
                const tokens = data.tokens_used || 0;
                const limit = data.token_limit || 128000;
                const pct = Math.min((tokens / limit) * 100, 100);

                let pClass = '';
                if (pct > 90) pClass = 'progress-danger';
                else if (pct > 75) pClass = 'progress-warning';

                const isActiveMark = name === active ? '<span class="badge" style="background:#fff; color:#000; margin-left:8px;">ACTIVE</span>' : '';

                const card = document.createElement('div');
                card.className = `card state-${data.state}`;

                // Randomize aura animations slightly for organic feel
                const animDelay = Math.random() * -5;

                card.innerHTML = `
                    <div class="dot-container">
                        <div class="aura" style="animation-delay: ${animDelay}s"></div>
                        <div class="aura" style="animation-delay: ${animDelay - 2}s"></div>
                        <div class="dot"></div>
                    </div>

                    <div class="details">
                        <h3>${name} ${isActiveMark}</h3>
                        <p>
                            <span class="badge" style="margin-right: 4px;">${data.agent_type || 'hermes'}</span>
                            <span class="badge">${data.model_name || 'gpt-4o'}</span>
                        </p>
                        <p style="color: ${isRunning ? '#10a37f' : '#f44336'}; margin-top: 10px;">${data.state.charAt(0).toUpperCase() + data.state.slice(1)}</p>

                        <div style="margin-top: 15px; margin-bottom: 20px;">
                            <div style="display:flex; justify-content:space-between; font-size:11px; color:#666;">
                                <span>Context Limit</span>
                                <span>${Math.round(pct)}%</span>
                            </div>
                            <div class="progress-bg">
                                <div class="progress-fill ${pClass}" style="width: ${pct}%"></div>
                            </div>
                        </div>

                        <button class="btn-danger" onclick="stopHarness('${name}')" style="font-size:11px; padding: 6px 12px;">Stop Node</button>
                    </div>
                `;
                grid.appendChild(card);
            }
        }

        // Initial load
        fetchState();
        // Auto-refresh every 5 seconds
        setInterval(fetchState, 5000);
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

if __name__ == '__main__':
    # Run the server on all interfaces, port 5000
    app.run(host='0.0.0.0', port=5000, debug=True)
