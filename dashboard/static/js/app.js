/**
 * Field Safety Monitoring System - Dashboard Application JS
 * Soldered Sensors: MQ5(33), MQ7(34), MQ135(35), HW-416-B PIR(26: true/false), HW-484 Sound(27), DHT11(15)
 */

let audioEnabled = true;
let audioCtx = null;
let currentThresholds = {};
let lastAlertCount = 0;
let lastRoverOnline = false;

// ================= AUDIO ALERT SYNTHESIZER =================
function playAlertBeep(level) {
    if (!audioEnabled) return;
    try {
        if (!audioCtx) {
            audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        }
        if (audioCtx.state === 'suspended') {
            audioCtx.resume();
        }
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();
        osc.connect(gain);
        gain.connect(audioCtx.destination);

        if (level === 'CRITICAL') {
            osc.frequency.setValueAtTime(880, audioCtx.currentTime);
            osc.frequency.setValueAtTime(660, audioCtx.currentTime + 0.15);
            gain.gain.setValueAtTime(0.3, audioCtx.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.4);
            osc.start();
            osc.stop(audioCtx.currentTime + 0.4);
        } else if (level === 'CONNECT') {
            // Pleasant double chime on connection
            osc.frequency.setValueAtTime(587.33, audioCtx.currentTime); // D5
            osc.frequency.setValueAtTime(880, audioCtx.currentTime + 0.12); // A5
            gain.gain.setValueAtTime(0.2, audioCtx.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.35);
            osc.start();
            osc.stop(audioCtx.currentTime + 0.35);
        } else {
            osc.frequency.setValueAtTime(523.25, audioCtx.currentTime);
            gain.gain.setValueAtTime(0.15, audioCtx.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.25);
            osc.start();
            osc.stop(audioCtx.currentTime + 0.25);
        }
    } catch (e) {
        console.warn("Audio alert error:", e);
    }
}

document.getElementById('btn-audio-toggle').addEventListener('click', function () {
    audioEnabled = !audioEnabled;
    this.innerHTML = audioEnabled 
        ? '<i class="fa-solid fa-volume-high text-cyan-400"></i>'
        : '<i class="fa-solid fa-volume-xmark text-slate-500"></i>';
});

// ================= DATA POLLING & RENDERING =================

async function fetchSystemData() {
    try {
        const res = await fetch('/api/data');
        if (!res.ok) return;
        const data = await res.json();

        // 1. Update Header
        document.getElementById('header-cp-count').innerText = data.checkpoints.length;
        document.getElementById('header-band-count').innerText = data.bands.length;
        document.getElementById('server-clock').innerText = data.server_time.split(' ')[1] || '--:--:--';
        currentThresholds = data.thresholds || {};

        // Header Rover Status Badge
        const roverStatusHeader = document.getElementById('header-rover-status');
        if (data.rover.is_online) {
            roverStatusHeader.innerHTML = `<span class="text-emerald-400"><i class="fa-solid fa-circle text-[8px] animate-pulse mr-1"></i>ONLINE (CP-${data.rover.connected_checkpoint_id || 1})</span>`;
        } else {
            roverStatusHeader.innerHTML = `<span class="text-slate-400">STOPPED / IDLE</span>`;
        }

        // 2. Render Alerts
        renderAlerts(data.alerts);

        // 3. Render Personnel Vitals
        renderWorkers(data.bands);

        // 4. Render Checkpoints
        renderCheckpoints(data.checkpoints);

        // 5. Render Rover Telemetry & Connection Status
        renderRover(data.rover);

        // 6. Render Mesh Topology
        renderTopology(data.checkpoints, data.bands);

    } catch (err) {
        console.error("Fetch error:", err);
    }
}

function renderAlerts(alertsList) {
    const feed = document.getElementById('alerts-feed');
    const badge = document.getElementById('alert-badge-count');

    if (!alertsList || alertsList.length === 0) {
        feed.innerHTML = `
            <div class="bg-slate-900/60 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-400 flex items-center justify-between">
                <span class="flex items-center gap-2">
                    <i class="fa-solid fa-circle-check text-emerald-400"></i>
                    <span>All site sensors & worker vitals within nominal limits.</span>
                </span>
                <span class="font-mono text-[10px] text-slate-500">Live</span>
            </div>`;
        badge.innerText = '0 Active';
        badge.className = 'bg-slate-800 text-slate-400 text-[10px] px-2 py-0.5 rounded-full';
        lastAlertCount = 0;
        return;
    }

    if (alertsList.length > lastAlertCount) {
        const newest = alertsList[0];
        playAlertBeep(newest.level);
    }
    lastAlertCount = alertsList.length;

    badge.innerText = `${alertsList.length} Incidents`;
    badge.className = 'bg-rose-950 text-rose-300 text-[10px] px-2 py-0.5 rounded-full border border-rose-500/40 animate-pulse';

    feed.innerHTML = alertsList.map(a => {
        const isCrit = a.level === 'CRITICAL';
        const isInfo = a.level === 'INFO';
        let bgStyle = 'bg-amber-950/40 border-amber-600/40 text-amber-200';
        let icon = 'fa-circle-exclamation text-amber-400';

        if (isCrit) {
            bgStyle = 'bg-rose-950/40 border-rose-600/40 text-rose-200';
            icon = 'fa-triangle-exclamation text-rose-400';
        } else if (isInfo) {
            bgStyle = 'bg-cyan-950/40 border-cyan-600/40 text-cyan-200';
            icon = 'fa-circle-info text-cyan-400';
        }

        return `
            <div class="${bgStyle} border rounded-lg px-3 py-2 text-xs flex items-center justify-between transition-all">
                <div class="flex items-center gap-2.5">
                    <i class="fa-solid ${icon}"></i>
                    <div>
                        <span class="font-bold mr-1">[${a.category}]</span>
                        <span class="font-semibold text-slate-300">${a.device_id}:</span>
                        <span>${a.message}</span>
                    </div>
                </div>
                <span class="font-mono text-[10px] text-slate-400 ml-2 whitespace-nowrap">${a.timestamp}</span>
            </div>
        `;
    }).join('');
}

function renderWorkers(bands) {
    const grid = document.getElementById('workers-grid');
    if (!bands || bands.length === 0) {
        grid.innerHTML = `
            <div class="bg-slate-900/70 border border-slate-800 rounded-xl p-5 text-center text-slate-500 text-xs col-span-full">
                No active worker monitoring bands transmitting.
            </div>`;
        return;
    }

    grid.innerHTML = bands.map(b => {
        const isHypoxia = b.spo2 < (currentThresholds.spo2_critical || 90);
        const isWarn = b.spo2 < (currentThresholds.spo2_warning || 94);
        const isTachycardia = b.pulse_bpm > (currentThresholds.pulse_max || 120);

        let statusBadge = `<span class="bg-emerald-950 text-emerald-300 border border-emerald-500/30 text-[10px] px-2 py-0.5 rounded-full font-bold">NORMAL</span>`;
        let cardGlow = 'border-slate-800';

        if (isHypoxia) {
            statusBadge = `<span class="bg-rose-950 text-rose-300 border border-rose-500/50 text-[10px] px-2 py-0.5 rounded-full font-bold animate-pulse">CRITICAL HYPOXIA</span>`;
            cardGlow = 'border-rose-600 alert-critical-glow';
        } else if (isWarn || isTachycardia) {
            statusBadge = `<span class="bg-amber-950 text-amber-300 border border-amber-500/40 text-[10px] px-2 py-0.5 rounded-full font-bold">WARNING</span>`;
            cardGlow = 'border-amber-600/60';
        }

        return `
            <div class="bg-slate-900/90 border ${cardGlow} rounded-2xl p-5 shadow-lg relative overflow-hidden flex flex-col justify-between">
                <div class="flex justify-between items-start mb-4">
                    <div>
                        <div class="flex items-center gap-2">
                            <h3 class="font-bold text-sm text-slate-100">${b.worker_name}</h3>
                            <span class="text-[10px] text-slate-400 font-mono">ID #${b.band_id}</span>
                        </div>
                        <p class="text-[11px] text-slate-500 flex items-center gap-1 mt-0.5">
                            <i class="fa-solid fa-route text-cyan-500"></i> Relayed via Checkpoint ${b.relayed_by} (${b.hops} hop)
                        </p>
                    </div>
                    ${statusBadge}
                </div>

                <div class="grid grid-cols-2 gap-3 mb-4">
                    <!-- Heart Rate -->
                    <div class="bg-slate-950/80 border border-slate-800/80 rounded-xl p-3 flex items-center gap-3">
                        <div class="w-10 h-10 rounded-lg bg-rose-950/60 border border-rose-800/50 flex items-center justify-center text-rose-400">
                            <i class="fa-solid fa-heart-pulse text-lg ${b.pulse_bpm > 0 ? 'heart-pulse' : ''}"></i>
                        </div>
                        <div>
                            <div class="text-[10px] text-slate-400 uppercase font-semibold">Pulse Rate</div>
                            <div class="text-xl font-bold font-mono ${isTachycardia ? 'text-rose-400' : 'text-slate-100'}">
                                ${b.pulse_bpm.toFixed(0)} <span class="text-xs font-normal text-slate-400">BPM</span>
                            </div>
                        </div>
                    </div>

                    <!-- SpO2 -->
                    <div class="bg-slate-950/80 border border-slate-800/80 rounded-xl p-3 flex items-center gap-3">
                        <div class="w-10 h-10 rounded-lg bg-cyan-950/60 border border-cyan-800/50 flex items-center justify-center text-cyan-400">
                            <i class="fa-solid fa-lungs text-lg"></i>
                        </div>
                        <div>
                            <div class="text-[10px] text-slate-400 uppercase font-semibold">Oxygen (SpO2)</div>
                            <div class="text-xl font-bold font-mono ${isHypoxia ? 'text-rose-400' : (isWarn ? 'text-amber-400' : 'text-cyan-300')}">
                                ${b.spo2.toFixed(1)}<span class="text-xs font-normal text-slate-400">%</span>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Footer details -->
                <div class="flex justify-between items-center text-[11px] text-slate-400 border-t border-slate-800/60 pt-2.5">
                    <span class="flex items-center gap-1.5">
                        <i class="fa-solid fa-battery-three-quarters text-emerald-400"></i> ${b.battery_pct}% Battery
                    </span>
                    <span class="font-mono text-[10px] text-slate-500">Live ESP-NOW</span>
                </div>
            </div>
        `;
    }).join('');
}

function renderCheckpoints(cps) {
    const grid = document.getElementById('checkpoints-grid');
    if (!cps || cps.length === 0) {
        grid.innerHTML = `
            <div class="bg-slate-900/70 border border-slate-800 rounded-xl p-5 text-center text-slate-500 text-xs col-span-full">
                No active checkpoints reporting.
            </div>`;
        return;
    }

    grid.innerHTML = cps.map(cp => {
        const isGasCrit = cp.mq5 >= (currentThresholds.mq5_critical || 600) || cp.mq7 >= (currentThresholds.mq7_critical || 450);
        const isSoundCrit = cp.sound_level >= (currentThresholds.sound_spike_threshold || 350);
        
        let cardGlow = 'border-slate-800';
        if (isGasCrit || isSoundCrit) {
            cardGlow = 'border-rose-600 alert-critical-glow';
        }

        return `
            <div class="bg-slate-900/90 border ${cardGlow} rounded-2xl p-5 shadow-lg space-y-4">
                <div class="flex justify-between items-start border-b border-slate-800/70 pb-3">
                    <div>
                        <div class="flex items-center gap-2">
                            <span class="w-2.5 h-2.5 rounded-full bg-cyan-400 animate-pulse"></span>
                            <h3 class="font-bold text-sm text-slate-100">${cp.location}</h3>
                        </div>
                        <div class="text-[11px] text-slate-400 font-mono mt-0.5">ESP32 Checkpoint #${cp.checkpoint_id}</div>
                    </div>
                    <span class="text-xs px-2.5 py-1 rounded-full bg-slate-800 text-slate-300 font-mono">
                        ${cp.temperature.toFixed(1)}°C | ${cp.humidity.toFixed(0)}%
                    </span>
                </div>

                <div class="space-y-2 text-xs">
                    <div>
                        <div class="flex justify-between text-[11px] mb-1">
                            <span class="text-slate-400">MQ5 Combustible (GPIO 33):</span>
                            <span class="font-mono font-bold ${cp.mq5 > 350 ? 'text-rose-400' : 'text-slate-200'}">${cp.mq5}</span>
                        </div>
                        <div class="w-full h-1.5 bg-slate-800 rounded-full overflow-hidden">
                            <div class="h-full bg-amber-500 rounded-full" style="width: ${Math.min(100, (cp.mq5 / 800) * 100)}%"></div>
                        </div>
                    </div>

                    <div>
                        <div class="flex justify-between text-[11px] mb-1">
                            <span class="text-slate-400">MQ7 Carbon Monoxide (GPIO 34):</span>
                            <span class="font-mono font-bold ${cp.mq7 > 250 ? 'text-rose-400' : 'text-slate-200'}">${cp.mq7}</span>
                        </div>
                        <div class="w-full h-1.5 bg-slate-800 rounded-full overflow-hidden">
                            <div class="h-full bg-rose-500 rounded-full" style="width: ${Math.min(100, (cp.mq7 / 600) * 100)}%"></div>
                        </div>
                    </div>

                    <div>
                        <div class="flex justify-between text-[11px] mb-1">
                            <span class="text-slate-400">MQ135 Air Quality (GPIO 35):</span>
                            <span class="font-mono font-bold ${cp.mq135 > 350 ? 'text-rose-400' : 'text-slate-200'}">${cp.mq135}</span>
                        </div>
                        <div class="w-full h-1.5 bg-slate-800 rounded-full overflow-hidden">
                            <div class="h-full bg-cyan-500 rounded-full" style="width: ${Math.min(100, (cp.mq135 / 700) * 100)}%"></div>
                        </div>
                    </div>
                </div>

                <div class="grid grid-cols-2 gap-2 pt-1 text-center">
                    <div class="bg-slate-950/70 border border-slate-800 p-2.5 rounded-xl">
                        <div class="text-[10px] text-slate-400 uppercase font-semibold mb-1">PIR Motion (HW-416-B: GPIO 26)</div>
                        <span class="inline-block text-[10px] font-bold px-2.5 py-0.5 rounded-full ${cp.pir_motion ? 'bg-amber-950 text-amber-300 border border-amber-500/40 animate-pulse' : 'bg-slate-800 text-slate-400'}">
                            ${cp.pir_motion ? 'MOTION DETECTED' : 'CLEAR / NO MOTION'}
                        </span>
                    </div>

                    <div class="bg-slate-950/70 border border-slate-800 p-2.5 rounded-xl">
                        <div class="text-[10px] text-slate-400 uppercase font-semibold mb-1">Sound Peak (HW-484: GPIO 27)</div>
                        <span class="font-mono text-xs font-bold ${isSoundCrit ? 'text-rose-400 font-extrabold animate-pulse' : 'text-slate-200'}">
                            ${cp.sound_level} ADC ${isSoundCrit ? '(SPIKE!)' : ''}
                        </span>
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

function renderRover(rover) {
    document.getElementById('rover-battery-val').innerText = `${rover.battery_pct || 90}%`;
    document.getElementById('rover-hud-status').innerText = `STATUS: ${rover.command_name} | SPEED: ${rover.speed}`;

    const banner = document.getElementById('rover-connection-banner');
    const cpLinkText = document.getElementById('rover-checkpoint-link');

    if (rover.is_online) {
        const cpId = rover.connected_checkpoint_id || 1;
        const cpLoc = rover.connected_checkpoint_location || 'Zone-A (Main Shaft)';

        banner.className = 'bg-emerald-950/70 border border-emerald-500/60 text-emerald-300 px-4 py-2.5 rounded-xl text-xs font-semibold flex items-center justify-between shadow-lg transition-all animate-pulse';
        banner.innerHTML = `
            <div class="flex items-center gap-2.5">
                <i class="fa-solid fa-circle-check text-emerald-400 text-sm"></i>
                <span>ROVER CONNECTED TO CHECKPOINT ${cpId} (${cpLoc}) - TELEMETRY & STEERING LINK ACTIVE</span>
            </div>
            <span class="text-[10px] font-mono bg-emerald-900/80 px-2 py-0.5 rounded text-emerald-200 border border-emerald-600/40">ESP-NOW LINK OK</span>
        `;

        if (cpLinkText) {
            cpLinkText.innerHTML = `<span class="text-emerald-400">CHECKPOINT ${cpId} (${cpLoc})</span>`;
        }

        if (!lastRoverOnline) {
            playAlertBeep('CONNECT');
        }
        lastRoverOnline = true;

    } else {
        banner.className = 'bg-slate-900/80 border border-slate-700/60 text-slate-400 px-4 py-2.5 rounded-xl text-xs font-semibold flex items-center justify-between shadow-lg transition-all';
        banner.innerHTML = `
            <div class="flex items-center gap-2">
                <i class="fa-solid fa-satellite-dish text-amber-400"></i>
                <span>WAITING FOR ROVER TELEMETRY / CHECKPOINT HEARTBEAT...</span>
            </div>
            <span class="text-[10px] font-mono text-slate-500">STANDBY</span>
        `;

        if (cpLinkText) {
            cpLinkText.innerHTML = `<span class="text-slate-500">OFFLINE / SEARCHING</span>`;
        }
        lastRoverOnline = false;
    }
}

function renderTopology(cps, bands) {
    const topo = document.getElementById('mesh-topology-view');
    topo.innerHTML = `
        <div class="bg-slate-950/80 border border-slate-800 p-3 rounded-xl">
            <i class="fa-solid fa-user-shield text-emerald-400 text-lg mb-1"></i>
            <div class="font-bold text-slate-200">Worker Bands (${bands.length})</div>
            <div class="text-[10px] text-slate-400">ESP-NOW Broadcast</div>
        </div>
        <div class="bg-slate-950/80 border border-slate-800 p-3 rounded-xl">
            <i class="fa-solid fa-tower-broadcast text-cyan-400 text-lg mb-1"></i>
            <div class="font-bold text-slate-200">Nearest Checkpoint</div>
            <div class="text-[10px] text-slate-400">ESP-NOW Ingestion</div>
        </div>
        <div class="bg-slate-950/80 border border-slate-800 p-3 rounded-xl">
            <i class="fa-solid fa-arrows-split-up-and-left text-purple-400 text-lg mb-1"></i>
            <div class="font-bold text-slate-200">Mesh Hop Relay</div>
            <div class="text-[10px] text-slate-400">Failover Forwarding</div>
        </div>
        <div class="bg-slate-950/80 border border-cyan-500/40 p-3 rounded-xl bg-cyan-950/20">
            <i class="fa-solid fa-server text-cyan-400 text-lg mb-1"></i>
            <div class="font-bold text-cyan-300">Station Hotspot</div>
            <div class="text-[10px] text-slate-400">HTTP REST Ingestion</div>
        </div>
    `;
}

// ================= ROVER CONTROL =================

let speedSlider = document.getElementById('rover-speed-slider');
speedSlider.addEventListener('input', (e) => {
    document.getElementById('rover-speed-val').innerText = `${e.target.value} / 255`;
});

async function sendRoverCmd(cmd) {
    const speed = parseInt(document.getElementById('rover-speed-slider').value);
    try {
        await fetch('/api/rover/command', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({command: cmd, speed: speed})
        });
    } catch (e) {
        console.error("Rover command error:", e);
    }
}

// Keyboard steering controls
let activeKeys = {};
window.addEventListener('keydown', (e) => {
    if (["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Space", "KeyW", "KeyA", "KeyS", "KeyD"].includes(e.code)) {
        e.preventDefault();
    }
    if (activeKeys[e.code]) return;
    activeKeys[e.code] = true;

    if (e.code === 'KeyW' || e.code === 'ArrowUp') sendRoverCmd('FORWARD');
    else if (e.code === 'KeyS' || e.code === 'ArrowDown') sendRoverCmd('BACKWARD');
    else if (e.code === 'KeyA' || e.code === 'ArrowLeft') sendRoverCmd('LEFT');
    else if (e.code === 'KeyD' || e.code === 'ArrowRight') sendRoverCmd('RIGHT');
    else if (e.code === 'Space') sendRoverCmd('STOP');
});

window.addEventListener('keyup', (e) => {
    delete activeKeys[e.code];
    if (['KeyW', 'KeyS', 'KeyA', 'KeyD', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(e.code)) {
        sendRoverCmd('STOP');
    }
});

// ================= CAMERA & CHARTS REFRESH =================

function refreshCharts() {
    const ts = Date.now();
    document.getElementById('chart-gas').src = `/api/charts/gas.png?t=${ts}`;
    document.getElementById('chart-vitals').src = `/api/charts/vitals.png?t=${ts}`;
    document.getElementById('chart-noise').src = `/api/charts/noise.png?t=${ts}`;
    document.getElementById('chart-environment').src = `/api/charts/environment.png?t=${ts}`;
}

function refreshCamera() {
    const ts = Date.now();
    document.getElementById('rover-camera-img').src = `/api/camera/latest.jpg?t=${ts}`;
}

// ================= ALERTS ACK & THRESHOLDS =================

async function clearAlerts() {
    await fetch('/api/alerts/ack', {method: 'POST'});
    fetchSystemData();
}

function openThresholdModal() {
    document.getElementById('th-spo2-critical').value = currentThresholds.spo2_critical || 90.0;
    document.getElementById('th-mq5-critical').value = currentThresholds.mq5_critical || 600;
    document.getElementById('th-mq7-critical').value = currentThresholds.mq7_critical || 450;
    document.getElementById('th-mq135-critical').value = currentThresholds.mq135_critical || 550;
    document.getElementById('th-sound-spike').value = currentThresholds.sound_spike_threshold || 350;

    const modal = document.getElementById('threshold-modal');
    modal.classList.remove('hidden');
    modal.classList.add('flex');
}

function closeThresholdModal() {
    const modal = document.getElementById('threshold-modal');
    modal.classList.add('hidden');
    modal.classList.remove('flex');
}

async function saveThresholds() {
    const payload = {
        spo2_critical: parseFloat(document.getElementById('th-spo2-critical').value),
        mq5_critical: parseFloat(document.getElementById('th-mq5-critical').value),
        mq7_critical: parseFloat(document.getElementById('th-mq7-critical').value),
        mq135_critical: parseFloat(document.getElementById('th-mq135-critical').value),
        sound_spike_threshold: parseFloat(document.getElementById('th-sound-spike').value)
    };

    await fetch('/api/thresholds', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
    });

    closeThresholdModal();
    fetchSystemData();
    refreshCharts();
}

// ================= INITIALIZATION TIMERS =================

window.addEventListener('DOMContentLoaded', () => {
    fetchSystemData();
    refreshCharts();

    setInterval(fetchSystemData, 1000);
    setInterval(refreshCharts, 2000);
    setInterval(refreshCamera, 300);
});
