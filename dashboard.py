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

    if not name:
        return jsonify({"error": "Missing harness name"}), 400

    orchestrator.load_state()
    success = orchestrator.start(name, agent_type=agent_type)
    if success:
        return jsonify({"status": "success", "message": f"Started {name} as {agent_type}"})
    return jsonify({"error": "Failed to start harness or already exists"}), 500

# --- Frontend Visual Dashboard ---

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Galactic Agent Command</title>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700&display=swap');

        body {
            font-family: 'Orbitron', sans-serif;
            background: #000;
            margin: 0;
            padding: 20px;
            color: #4df;
            min-height: 100vh;
            overflow-x: hidden;
            perspective: 1000px; /* 3D depth */
        }

        /* Dynamic Starfield & Nebula */
        body::before {
            content: "";
            position: fixed;
            top: 0; left: 0; width: 200vw; height: 200vh;
            background:
                radial-gradient(circle at 20% 30%, rgba(138, 43, 226, 0.15) 0%, transparent 40%),
                radial-gradient(circle at 80% 70%, rgba(68, 221, 255, 0.1) 0%, transparent 40%),
                radial-gradient(1px 1px at 20px 30px, #fff, rgba(0,0,0,0)),
                radial-gradient(2px 2px at 40px 70px, #fff, rgba(0,0,0,0)),
                radial-gradient(1px 1px at 50px 160px, #fff, rgba(0,0,0,0));
            background-size: 100% 100%, 100% 100%, 200px 200px, 300px 300px, 150px 150px;
            z-index: -1;
            animation: drift 100s linear infinite;
        }

        @keyframes drift { 0% { transform: translate(0, 0); } 100% { transform: translate(-10vw, -10vh); } }

        h1 {
            color: #ffe81f; /* Star Wars Yellow */
            text-align: center;
            text-transform: uppercase;
            letter-spacing: 5px;
            text-shadow: 0 0 20px rgba(255, 232, 31, 0.8);
            margin-bottom: 40px;
            animation: pulse-glow 3s infinite alternate;
        }
        @keyframes pulse-glow { from { text-shadow: 0 0 10px rgba(255,232,31,0.5); } to { text-shadow: 0 0 30px rgba(255,232,31,1); } }

        .controls { display: flex; justify-content: center; gap: 20px; margin-bottom: 30px; position: relative; z-index: 10; }

        button {
            padding: 12px 25px;
            background: rgba(0, 150, 255, 0.15);
            color: #4df;
            border: 1px solid #4df;
            border-radius: 8px;
            cursor: pointer;
            font-family: 'Orbitron', sans-serif;
            font-size: 14px;
            font-weight: bold;
            text-transform: uppercase;
            box-shadow: 0 0 10px rgba(68, 221, 255, 0.4);
            transition: all 0.2s ease-in-out;
            backdrop-filter: blur(4px);
        }
        button:hover {
            background: rgba(0, 150, 255, 0.4);
            box-shadow: 0 0 20px rgba(68, 221, 255, 0.8);
            transform: translateY(-2px) scale(1.05);
        }
        .btn-health { border-color: #28a745; color: #28a745; box-shadow: 0 0 10px rgba(40, 167, 69, 0.4); }
        .btn-health:hover { background: rgba(40, 167, 69, 0.3); box-shadow: 0 0 20px rgba(40, 167, 69, 0.8); }

        .btn-danger { border-color: #ff3366; color: #ff3366; box-shadow: 0 0 10px rgba(255, 51, 102, 0.4); background: rgba(255,51,102,0.1); }
        .btn-danger:hover { background: rgba(255, 51, 102, 0.3); box-shadow: 0 0 25px rgba(255, 51, 102, 0.9); }

        /* 3D Grid container */
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(350px, 1fr));
            gap: 40px;
            max-width: 1400px;
            margin: 0 auto;
            padding: 40px 0;
            transform-style: preserve-3d;
        }

        /* Holographic Galaxy Cards */
        .card {
            background: rgba(5, 10, 20, 0.6);
            border-radius: 50%;
            padding: 50px;
            aspect-ratio: 1 / 1;
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            text-align: center;
            border: 2px solid rgba(255,255,255,0.1);
            box-shadow: inset 0 0 50px rgba(0,0,0,0.8), 0 0 20px rgba(0,0,0,0.5);
            position: relative;
            overflow: visible; /* Let holograms bleed out */
            backdrop-filter: blur(10px);
            transition: transform 0.5s cubic-bezier(0.175, 0.885, 0.32, 1.275);
            animation: float 6s ease-in-out infinite;
        }

        .card:hover {
            transform: translateZ(50px) scale(1.05);
            z-index: 100;
        }

        /* Surreal Galaxy Swirl */
        .card::before {
            content: '';
            position: absolute;
            top: -20%; left: -20%; width: 140%; height: 140%;
            border-radius: 50%;
            background: conic-gradient(from 0deg, transparent, rgba(68,221,255,0.3) 20%, transparent 40%, rgba(138,43,226,0.3) 60%, transparent 80%);
            animation: rotate 15s linear infinite;
            z-index: -1;
            filter: blur(15px);
        }

        /* LangGraph Data Streams (Particles connecting logic) */
        .card::after {
            content: '';
            position: absolute;
            top: 50%; left: 50%; width: 100%; height: 100%;
            border: 1px dashed rgba(255,255,255,0.1);
            border-radius: 50%;
            transform: translate(-50%, -50%);
            animation: rotate 20s linear infinite reverse;
            pointer-events: none;
        }

        @keyframes float { 0% { transform: translateY(0px) rotateX(0deg); } 50% { transform: translateY(-15px) rotateX(5deg); } 100% { transform: translateY(0px) rotateX(0deg); } }
        @keyframes rotate { 100% { transform: translate(-50%, -50%) rotate(360deg); } }

        /* Status Colors */
        .state-running { border-color: rgba(0,255,136,0.5); box-shadow: 0 0 30px rgba(0,255,136,0.3); }
        .state-running::before { background: conic-gradient(from 0deg, transparent, rgba(0,255,136,0.3) 20%, transparent 40%); }

        .state-stale { border-color: #ff3366; box-shadow: 0 0 30px rgba(255,51,102,0.4); animation: pulse-red 2s infinite; }
        .state-stale::before { animation-play-state: paused; background: radial-gradient(circle, rgba(255,51,102,0.2) 0%, transparent 70%); filter: blur(5px); }

        .state-context_exceeded { border-color: #ffcc00; box-shadow: 0 0 30px rgba(255,204,0,0.4); }
        .state-context_exceeded::before { background: conic-gradient(from 0deg, transparent, rgba(255,204,0,0.4) 20%, transparent 40%); }

        @keyframes pulse-red { 0% { box-shadow: 0 0 20px rgba(255,51,102,0.3); } 50% { box-shadow: 0 0 50px rgba(255,51,102,0.7); } 100% { box-shadow: 0 0 20px rgba(255,51,102,0.3); } }

        .card h3 {
            margin: 0 0 20px 0;
            color: #fff;
            text-transform: uppercase;
            letter-spacing: 3px;
            font-size: 1.4rem;
            z-index: 2;
            text-shadow: 0 2px 4px rgba(0,0,0,0.8);
        }

        .badge {
            font-size: 10px;
            padding: 4px 8px;
            border-radius: 4px;
            background: rgba(255,255,255,0.1);
            border: 1px solid rgba(255,255,255,0.3);
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        .progress-bg {
            background: rgba(0,0,0,0.5);
            border: 1px solid #4df;
            border-radius: 2px;
            height: 6px;
            width: 100%;
            margin-top: 5px;
            overflow: hidden;
        }
        .progress-fill { background: #4df; height: 100%; width: 0%; transition: width 0.3s; box-shadow: 0 0 5px #4df; }
        .progress-warning { background: #ffc107; box-shadow: 0 0 5px #ffc107; border-color: #ffc107; }
        .progress-danger { background: #dc3545; box-shadow: 0 0 5px #dc3545; border-color: #dc3545; }

        .details { z-index: 1; width: 80%; }
        .details p { margin: 8px 0; font-size: 12px; color: #aaa; }
        .details strong { color: #fff; }

        .form-group { display: flex; gap: 10px; margin-bottom: 40px; justify-content: center; }
        select, input {
            padding: 10px;
            background: rgba(0,0,0,0.5);
            color: #4df;
            border: 1px solid #4df;
            border-radius: 4px;
            font-family: 'Orbitron', sans-serif;
        }
        select:focus, input:focus { outline: none; box-shadow: 0 0 10px rgba(68, 221, 255, 0.5); }
    </style>
</head>
<body>
    <h1>Galactic Agent Command</h1>

    <div class="controls">
        <button onclick="fetchState()">Refresh Sector</button>
        <button class="btn-health" onclick="runHealthCheck()">Sensor Scan (Health)</button>
    </div>

    <div class="form-group">
        <input type="text" id="newName" placeholder="New Node ID" />
        <select id="newType">
            <option value="hermes">LangGraph Node: Hermes</option>
            <option value="jcode">LangGraph Node: JCode</option>
            <option value="opencode">LangGraph Node: OpenCode</option>
            <option value="antigravity">LangGraph Node: Antigravity</option>
        </select>
        <button onclick="startHarness()" style="box-shadow: 0 0 15px rgba(138,43,226,0.6); border-color: #8a2be2; color: #d4a5ff;">Initialize Graph Node</button>
    </div>

    <div class="grid" id="harnessGrid">
        <!-- Galaxies will be injected here via JS -->
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
            if (!name) return alert("Please provide a name.");

            await fetch('/api/start', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ name, agent_type: type })
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

                const isActiveMark = name === active ? '<br><span class="badge" style="background:#ffe81f; color:#000; border:none; margin-top:8px; display:inline-block; box-shadow:0 0 10px #ffe81f;">ACTIVE SUB-GRAPH</span>' : '';

                const card = document.createElement('div');
                card.className = `card state-${data.state}`;
                // Delay animation slightly for each card to make it look organic
                card.style.animationDelay = `${Math.random() * 2}s`;

                card.innerHTML = `
                    <h3>${name} ${isActiveMark}</h3>
                    <div class="details">
                        <p><strong>Node Class:</strong> <span class="badge">${data.agent_type || 'hermes'}</span></p>
                        <p><strong>LangGraph State:</strong> <span style="color:${isRunning ? '#0f0' : '#f00'}">${data.state.toUpperCase()}</span></p>
                        <p><strong>Executor:</strong> <code style="color:#0f0;">> ${data.background_cmd || 'N/A'}</code></p>

                        <div style="margin-top: 25px;">
                            <div style="display:flex; justify-content:space-between; font-size:11px; color:#4df; text-transform:uppercase; margin-bottom: 5px;">
                                <span>Context Window:</span>
                                <span>${tokens.toLocaleString()} / ${limit.toLocaleString()}</span>
                            </div>
                            <div class="progress-bg">
                                <div class="progress-fill ${pClass}" style="width: ${pct}%"></div>
                            </div>
                        </div>
                    </div>
                    <div style="margin-top: 30px; z-index: 2;">
                        <button class="btn-danger" onclick="stopHarness('${name}')">Sever Node</button>
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
