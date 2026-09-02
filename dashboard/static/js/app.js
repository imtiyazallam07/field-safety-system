/**
 * Field Safety Monitoring System - Dashboard Application JS
 * Dedicated Contextual Operational Pages & Real-Time Telemetry
 */

let audioEnabled = true;
let audioCtx = null;
let currentThresholds = {};
let lastAlertCount = 0;
let lastRoverOnline = false;
let currentView = 'overview';

// Optimization & Concurrency Guards
let lastAlertsSig = '';
let lastWorkersSig = '';
let lastCheckpointsSig = '';
let lastTopologySig = '';
let isDataFetching = false;
let isCameraLoading = false;

// ================= CONTEXTUAL VIEW & ROUTE SWITCHER =================

const VIEW_CONFIG = {
    'overview': { title: 'COMMAND CENTER', path: '/dashboard', navId: 'nav-overview' },
    'incidents': { title: 'INCIDENT STREAM', path: '/incidents', navId: 'nav-incidents' },
    'workers': { title: 'WORKER VITALS', path: '/workers', navId: 'nav-workers' },
    'checkpoints': { title: 'CHECKPOINT SUITE', path: '/checkpoints', navId: 'nav-checkpoints' },
    'charts': { title: 'TREND TELEMETRY', path: '/telemetry', navId: 'nav-charts' },
    'rover': { title: 'RECON ROVER', path: '/rover', navId: 'nav-rover' },
    'topology': { title: 'MESH NETWORK', path: '/network', navId: 'nav-topology' }
};

const PATH_TO_VIEW = {
    '/dashboard': 'overview',
    '/station': 'overview',
    '/incidents': 'incidents',
    '/workers': 'workers',
    '/checkpoints': 'checkpoints',
    '/telemetry': 'charts',
    '/rover': 'rover',
    '/network': 'topology'
};

function handleNavClick(event, viewName, urlPath) {
    if (event) event.preventDefault();
    switchView(viewName);
    if (window.location.pathname !== urlPath) {
        history.pushState({ view: viewName }, '', urlPath);
    }
}

function switchView(viewName) {
    if (!VIEW_CONFIG[viewName]) viewName = 'overview';
    currentView = viewName;

    // 1. Hide all operational views and show the selected view
    document.querySelectorAll('.operational-view').forEach(view => {
        view.classList.add('hidden');
    });
    const targetView = document.getElementById(`view-${viewName}`);
    if (targetView) {
        targetView.classList.remove('hidden');
    }

    // 2. Update navigation item active state styles
    document.querySelectorAll('.nav-item').forEach(nav => {
        nav.className = 'nav-item w-full flex items-center gap-3 px-3.5 py-2.5 text-on-surface-variant hover:bg-surface-container-highest hover:text-on-surface transition-all rounded-sm text-left group';
    });
    const activeNavId = VIEW_CONFIG[viewName].navId;
    const activeNav = document.getElementById(activeNavId);
    if (activeNav) {
        activeNav.className = 'nav-item w-full flex items-center gap-3 px-3.5 py-2.5 bg-surface-container-high border-l-4 border-hazard-yellow text-hazard-yellow rounded-r-sm font-bold transition-all text-left group';
    }

    // 3. Update top app bar header badge
    const viewBadge = document.getElementById('current-view-badge');
    if (viewBadge) {
        viewBadge.innerText = VIEW_CONFIG[viewName].title;
    }

    // Scroll main body to top
    const mainEl = document.querySelector('main');
    if (mainEl) mainEl.scrollTop = 0;

    // Immediate refresh for view-relevant assets
    refreshCharts();
    refreshCamera();
}

window.addEventListener('popstate', (event) => {
    if (event.state && event.state.view) {
        switchView(event.state.view);
    } else {
        const path = window.location.pathname;
        const mappedView = PATH_TO_VIEW[path] || 'overview';
        switchView(mappedView);
    }
});

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
            osc.frequency.setValueAtTime(587.33, audioCtx.currentTime);
            osc.frequency.setValueAtTime(880, audioCtx.currentTime + 0.12);
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
        ? '<span class="material-symbols-outlined text-sm align-middle text-hazard-yellow">volume_up</span>'
        : '<span class="material-symbols-outlined text-sm align-middle text-on-surface-variant">volume_off</span>';
});

// ================= DATA POLLING & RENDERING =================

async function fetchSystemData() {
    if (isDataFetching) return;
    isDataFetching = true;
    try {
        const res = await fetch('/api/data');
        if (!res.ok) return;
        const data = await res.json();

        // 1. Update Header & KPI Metrics
        const cpCountEl = document.getElementById('header-cp-count');
        if (cpCountEl && cpCountEl.innerText !== String(data.checkpoints.length)) {
            cpCountEl.innerText = data.checkpoints.length;
        }
        const bandCountEl = document.getElementById('header-band-count');
        if (bandCountEl && bandCountEl.innerText !== String(data.bands.length)) {
            bandCountEl.innerText = data.bands.length;
        }
        const clockEl = document.getElementById('server-clock');
        const newClock = (data.server_time && data.server_time.split(' ')[1]) || '--:--:--';
        if (clockEl && clockEl.innerText !== newClock) {
            clockEl.innerText = newClock;
        }
        currentThresholds = data.thresholds || {};

        // Rover Status KPI Badge
        const roverStatusHeader = document.getElementById('header-rover-status');
        if (roverStatusHeader) {
            if (data.rover && data.rover.is_online) {
                const desiredHtml = `<span class="text-industrial-green flex items-center gap-1"><span class="w-1.5 h-1.5 rounded-full bg-industrial-green animate-pulse"></span> ONLINE (CP-${data.rover.connected_checkpoint_id || 1})</span>`;
                if (roverStatusHeader.innerHTML !== desiredHtml) roverStatusHeader.innerHTML = desiredHtml;
            } else {
                const desiredHtml = `<span class="text-on-surface-variant">STOPPED / IDLE</span>`;
                if (roverStatusHeader.innerHTML !== desiredHtml) roverStatusHeader.innerHTML = desiredHtml;
            }
        }

        // 2. Render Alerts Stream (both in dedicated view and overview preview)
        renderAlerts(data.alerts);

        // 3. Render Personnel Vitals (both in dedicated view and overview preview)
        renderWorkers(data.bands);

        // 4. Render Checkpoints Grid (both in dedicated view and overview preview)
        renderCheckpoints(data.checkpoints);

        // 5. Render Rover Telemetry
        renderRover(data.rover);

        // 6. Render Mesh Topology
        renderTopology(data.checkpoints, data.bands);

    } catch (err) {
        console.error("Fetch error:", err);
    } finally {
        isDataFetching = false;
    }
}

function renderAlerts(alertsList) {
    const feed = document.getElementById('alerts-feed');
    const overviewFeed = document.getElementById('overview-alerts-feed');
    const badge = document.getElementById('alert-badge-count');
    const navBadge = document.getElementById('nav-alert-badge');

    if (!alertsList || alertsList.length === 0) {
        if (lastAlertsSig === 'empty') return;
        lastAlertsSig = 'empty';
        const emptyHtml = `
            <div class="bg-carbon-black border border-steel-gray rounded-sm px-3 py-2.5 text-xs text-on-surface-variant flex items-center justify-between font-mono">
                <span class="flex items-center gap-2">
                    <span class="material-symbols-outlined text-industrial-green text-sm">check_circle</span>
                    <span>All site sensors & worker biometrics nominal. No active safety incidents.</span>
                </span>
                <span class="text-[10px] text-on-surface-variant">Live</span>
            </div>`;
        if (feed) feed.innerHTML = emptyHtml;
        if (overviewFeed) overviewFeed.innerHTML = emptyHtml;
        if (badge) badge.innerText = '0 Active';
        if (navBadge) {
            navBadge.innerText = '0';
            navBadge.className = 'bg-surface-container text-on-surface-variant border border-steel-gray px-1.5 py-0.2 rounded text-[10px]';
        }
        lastAlertCount = 0;
        return;
    }

    const currentSig = alertsList.map(a => a.id + a.timestamp + a.level).join('|');
    if (currentSig === lastAlertsSig) return;
    lastAlertsSig = currentSig;

    if (alertsList.length > lastAlertCount) {
        const newest = alertsList[0];
        playAlertBeep(newest.level);
    }
    lastAlertCount = alertsList.length;

    if (badge) badge.innerText = `${alertsList.length} Active`;
    if (navBadge) {
        navBadge.innerText = `${alertsList.length}`;
        navBadge.className = 'bg-critical-red/20 text-critical-red border border-critical-red/40 px-1.5 py-0.2 rounded text-[10px] font-bold animate-pulse';
    }

    const html = alertsList.map(a => {
        const isCrit = a.level === 'CRITICAL';
        const isInfo = a.level === 'INFO';
        
        let borderClass = 'border-l-4 border-l-safety-orange border-steel-gray';
        let badgeColor = 'text-safety-orange bg-safety-orange/10 border-safety-orange/30';
        let icon = 'warning';
        let iconColor = 'text-safety-orange';

        if (isCrit) {
            borderClass = 'border-l-4 border-l-critical-red border-steel-gray shadow-[0_0_10px_rgba(255,0,0,0.15)]';
            badgeColor = 'text-critical-red bg-critical-red/20 border-critical-red/40 animate-pulse';
            icon = 'emergency';
            iconColor = 'text-critical-red';
        } else if (isInfo) {
            borderClass = 'border-l-4 border-l-hazard-yellow border-steel-gray';
            badgeColor = 'text-hazard-yellow bg-hazard-yellow/10 border-hazard-yellow/30';
            icon = 'info';
            iconColor = 'text-hazard-yellow';
        }

        return `
            <div class="bg-carbon-black border ${borderClass} rounded-sm px-3 py-2 text-xs flex items-center justify-between transition-all font-mono">
                <div class="flex items-center gap-2.5">
                    <span class="material-symbols-outlined ${iconColor} text-base">${icon}</span>
                    <div>
                        <span class="px-1.5 py-0.5 rounded-sm border ${badgeColor} text-[10px] font-bold mr-1.5">${a.category.toUpperCase()}</span>
                        <span class="font-bold text-on-surface">${a.device_id}:</span>
                        <span class="text-on-surface-variant ml-1">${a.message}</span>
                    </div>
                </div>
                <span class="text-[10px] text-on-surface-variant ml-3 whitespace-nowrap bg-surface-container-high px-2 py-0.5 rounded-sm border border-steel-gray">${a.timestamp}</span>
            </div>
        `;
    }).join('');

    if (feed) feed.innerHTML = html;
    if (overviewFeed) overviewFeed.innerHTML = html;
}

function renderWorkers(bands) {
    const grid = document.getElementById('workers-grid');
    const overviewGrid = document.getElementById('overview-workers-grid');

    if (!bands || bands.length === 0) {
        if (lastWorkersSig === 'empty') return;
        lastWorkersSig = 'empty';
        const emptyHtml = `
            <div class="industrial-panel rounded-sm p-6 text-center text-on-surface-variant font-mono text-xs col-span-full border border-steel-gray">
                No active worker biometric bands transmitting telemetry. Connect ESP32 Band #1 (MAX30100) to begin real-time ingestion.
            </div>`;
        if (grid) grid.innerHTML = emptyHtml;
        if (overviewGrid) overviewGrid.innerHTML = emptyHtml;
        return;
    }

    const currentSig = bands.map(b => `${b.band_id}_${b.pulse_bpm}_${b.spo2}_${b.battery_pct}_${b.hops}_${b.is_online}`).join('|');
    if (currentSig === lastWorkersSig) return;
    lastWorkersSig = currentSig;

    const html = bands.map(b => {
        const isHypoxia = b.spo2 < (currentThresholds.spo2_critical || 90);
        const isWarn = b.spo2 < (currentThresholds.spo2_warning || 94);
        const isTachycardia = b.pulse_bpm > (currentThresholds.pulse_max || 120);

        let statusBadge = `<span class="bg-industrial-green/10 text-industrial-green border border-industrial-green/30 text-[10px] px-2 py-0.5 rounded-sm font-bold font-mono">NOMINAL</span>`;
        let cardBorder = 'border-steel-gray';

        if (isHypoxia) {
            statusBadge = `<span class="bg-critical-red/20 text-critical-red border border-critical-red/50 text-[10px] px-2 py-0.5 rounded-sm font-bold font-mono animate-pulse">CRITICAL HYPOXIA</span>`;
            cardBorder = 'border-critical-red alert-critical-glow';
        } else if (isWarn || isTachycardia) {
            statusBadge = `<span class="bg-hazard-yellow/10 text-hazard-yellow border border-hazard-yellow/40 text-[10px] px-2 py-0.5 rounded-sm font-bold font-mono">WARNING</span>`;
            cardBorder = 'border-hazard-yellow/60';
        }

        return `
            <div class="industrial-panel border-2 ${cardBorder} rounded-sm p-4 relative overflow-hidden flex flex-col justify-between font-mono">
                <div class="flex justify-between items-start mb-3 pb-2 border-b border-steel-gray">
                    <div>
                        <div class="flex items-center gap-2">
                            <span class="material-symbols-outlined text-hazard-yellow text-base">engineering</span>
                            <h3 class="font-bold text-sm text-on-surface uppercase">${b.worker_name}</h3>
                            <span class="text-[10px] text-on-surface-variant">ID #${b.band_id}</span>
                        </div>
                        <p class="text-[10px] text-on-surface-variant flex items-center gap-1 mt-1">
                            <span class="material-symbols-outlined text-xs text-hazard-yellow">alt_route</span> Relayed via CP-${b.relayed_by} (${b.hops} hop)
                        </p>
                    </div>
                    ${statusBadge}
                </div>

                <div class="grid grid-cols-2 gap-3 mb-3">
                    <!-- Heart Rate -->
                    <div class="bg-carbon-black border border-steel-gray rounded-sm p-3 flex flex-col">
                        <div class="flex items-center justify-between text-[10px] text-on-surface-variant uppercase font-semibold mb-1">
                            <span>Heart Rate</span>
                            <span class="material-symbols-outlined text-critical-red text-sm ${b.pulse_bpm > 0 ? 'heart-pulse' : ''}">favorite</span>
                        </div>
                        <div class="flex items-baseline gap-1">
                            <span class="text-2xl font-bold ${isTachycardia ? 'text-critical-red' : 'text-on-surface'}">
                                ${b.pulse_bpm.toFixed(0)}
                            </span>
                            <span class="text-[10px] text-on-surface-variant">BPM</span>
                        </div>
                    </div>

                    <!-- SpO2 -->
                    <div class="bg-carbon-black border border-steel-gray rounded-sm p-3 flex flex-col">
                        <div class="flex items-center justify-between text-[10px] text-on-surface-variant uppercase font-semibold mb-1">
                            <span>Oxygen (SpO2)</span>
                            <span class="material-symbols-outlined text-hazard-yellow text-sm">air</span>
                        </div>
                        <div class="flex items-baseline gap-1">
                            <span class="text-2xl font-bold ${isHypoxia ? 'text-critical-red' : (isWarn ? 'text-hazard-yellow' : 'text-industrial-green')}">
                                ${b.spo2.toFixed(1)}
                            </span>
                            <span class="text-[10px] text-on-surface-variant">%</span>
                        </div>
                    </div>
                </div>

                <!-- Footer details -->
                <div class="flex justify-between items-center text-[10px] text-on-surface-variant border-t border-steel-gray pt-2">
                    <span class="flex items-center gap-1">
                        <span class="material-symbols-outlined text-industrial-green text-xs">battery_charging_full</span> ${b.battery_pct}% Battery
                    </span>
                    <span class="text-[10px] text-on-surface-variant">ESP-NOW LINK</span>
                </div>
            </div>
        `;
    }).join('');

    if (grid) grid.innerHTML = html;
    if (overviewGrid) overviewGrid.innerHTML = html;
}

function renderCheckpoints(cps) {
    const grid = document.getElementById('checkpoints-grid');
    const overviewGrid = document.getElementById('overview-checkpoints-grid');

    if (!cps || cps.length === 0) {
        if (lastCheckpointsSig === 'empty') return;
        lastCheckpointsSig = 'empty';
        const emptyHtml = `
            <div class="industrial-panel rounded-sm p-6 text-center text-on-surface-variant font-mono text-xs col-span-full border border-steel-gray">
                No active checkpoints reporting sensor telemetry. Listening on ESP-NOW / Wi-Fi Mesh...
            </div>`;
        if (grid) grid.innerHTML = emptyHtml;
        if (overviewGrid) overviewGrid.innerHTML = emptyHtml;
        return;
    }

    const currentSig = cps.map(cp => `${cp.checkpoint_id}_${cp.mq5}_${cp.mq7}_${cp.mq135}_${cp.sound_level}_${cp.pir_motion}_${cp.temperature}_${cp.humidity}_${cp.is_online}`).join('|');
    if (currentSig === lastCheckpointsSig) return;
    lastCheckpointsSig = currentSig;

    const html = cps.map(cp => {
        const isGasCrit = cp.mq5 >= (currentThresholds.mq5_critical || 600) || cp.mq7 >= (currentThresholds.mq7_critical || 450);
        const isSoundCrit = cp.sound_level >= (currentThresholds.sound_spike_threshold || 350);
        
        let cardBorder = 'border-steel-gray';
        if (isGasCrit || isSoundCrit) {
            cardBorder = 'border-critical-red alert-critical-glow';
        }

        return `
            <div class="industrial-panel border-2 ${cardBorder} rounded-sm p-4 space-y-3 font-mono">
                <div class="flex justify-between items-start border-b border-steel-gray pb-2.5">
                    <div>
                        <div class="flex items-center gap-2">
                            <span class="w-2 h-2 rounded-full bg-hazard-yellow animate-pulse"></span>
                            <h3 class="font-bold text-sm text-on-surface uppercase">${cp.location}</h3>
                        </div>
                        <div class="text-[10px] text-on-surface-variant mt-0.5">ESP32 Checkpoint Node #${cp.checkpoint_id}</div>
                    </div>
                    <span class="text-xs px-2 py-0.5 rounded-sm bg-carbon-black border border-steel-gray text-on-surface">
                        ${cp.temperature.toFixed(1)}°C | ${cp.humidity.toFixed(0)}%
                    </span>
                </div>

                <div class="space-y-2 text-xs">
                    <!-- MQ5 -->
                    <div>
                        <div class="flex justify-between text-[11px] mb-1">
                            <span class="text-on-surface-variant">MQ5 Combustible Gas (GPIO 33):</span>
                            <span class="font-bold ${cp.mq5 > 350 ? 'text-critical-red' : 'text-on-surface'}">${cp.mq5} <span class="text-[10px] text-on-surface-variant font-normal">ADC</span></span>
                        </div>
                        <div class="w-full h-1.5 bg-carbon-black border border-steel-gray rounded-sm overflow-hidden">
                            <div class="h-full bg-hazard-yellow rounded-sm transition-all" style="width: ${Math.min(100, (cp.mq5 / 800) * 100)}%"></div>
                        </div>
                    </div>

                    <!-- MQ7 -->
                    <div>
                        <div class="flex justify-between text-[11px] mb-1">
                            <span class="text-on-surface-variant">MQ7 Carbon Monoxide (GPIO 34):</span>
                            <span class="font-bold ${cp.mq7 > 250 ? 'text-critical-red' : 'text-on-surface'}">${cp.mq7} <span class="text-[10px] text-on-surface-variant font-normal">ADC</span></span>
                        </div>
                        <div class="w-full h-1.5 bg-carbon-black border border-steel-gray rounded-sm overflow-hidden">
                            <div class="h-full bg-critical-red rounded-sm transition-all" style="width: ${Math.min(100, (cp.mq7 / 600) * 100)}%"></div>
                        </div>
                    </div>

                    <!-- MQ135 -->
                    <div>
                        <div class="flex justify-between text-[11px] mb-1">
                            <span class="text-on-surface-variant">MQ135 Air Quality (GPIO 35):</span>
                            <span class="font-bold ${cp.mq135 > 350 ? 'text-critical-red' : 'text-on-surface'}">${cp.mq135} <span class="text-[10px] text-on-surface-variant font-normal">ADC</span></span>
                        </div>
                        <div class="w-full h-1.5 bg-carbon-black border border-steel-gray rounded-sm overflow-hidden">
                            <div class="h-full bg-safety-orange rounded-sm transition-all" style="width: ${Math.min(100, (cp.mq135 / 700) * 100)}%"></div>
                        </div>
                    </div>
                </div>

                <div class="grid grid-cols-2 gap-2 pt-1 text-center">
                    <div class="bg-carbon-black border border-steel-gray p-2 rounded-sm">
                        <div class="text-[9px] text-on-surface-variant uppercase font-semibold mb-1">PIR Motion (HW-416-B)</div>
                        <span class="inline-block text-[10px] font-bold px-2 py-0.5 rounded-sm ${cp.pir_motion ? 'bg-hazard-yellow/20 text-hazard-yellow border border-hazard-yellow/50 animate-pulse' : 'bg-surface-container-high text-on-surface-variant border border-steel-gray'}">
                            ${cp.pir_motion ? 'MOTION DETECTED' : 'CLEAR'}
                        </span>
                    </div>

                    <div class="bg-carbon-black border border-steel-gray p-2 rounded-sm">
                        <div class="text-[9px] text-on-surface-variant uppercase font-semibold mb-1">Sound (HW-484)</div>
                        <span class="text-xs font-bold ${isSoundCrit ? 'text-critical-red font-extrabold animate-pulse' : 'text-on-surface'}">
                            ${cp.sound_level} ADC ${isSoundCrit ? '⚠️' : ''}
                        </span>
                    </div>
                </div>
            </div>
        `;
    }).join('');

    if (grid) grid.innerHTML = html;
    if (overviewGrid) overviewGrid.innerHTML = html;
}

function renderRover(rover) {
    if (!rover) return;
    const battVal = document.getElementById('rover-battery-val');
    const hudVal = document.getElementById('rover-hud-status');
    const newBatt = `${rover.battery_pct || 90}%`;
    const newHud = `STATUS: ${rover.command_name || 'STOP'} | SPEED: ${rover.speed || 0}`;

    if (battVal && battVal.innerText !== newBatt) battVal.innerText = newBatt;
    if (hudVal && hudVal.innerText !== newHud) hudVal.innerText = newHud;

    const banner = document.getElementById('rover-connection-banner');
    const cpLinkText = document.getElementById('rover-checkpoint-link');

    if (rover.is_online) {
        const cpId = rover.connected_checkpoint_id || 1;
        const cpLoc = rover.connected_checkpoint_location || 'Zone-A (Main Shaft)';

        if (banner && (!lastRoverOnline || banner.dataset.status !== 'online')) {
            banner.dataset.status = 'online';
            banner.className = 'industrial-panel border-2 border-industrial-green text-industrial-green px-4 py-2.5 rounded-sm text-xs flex items-center justify-between transition-all';
            banner.innerHTML = `
                <div class="flex items-center gap-2">
                    <span class="material-symbols-outlined text-industrial-green text-base">check_circle</span>
                    <span class="font-bold">ROVER CONNECTED TO CHECKPOINT ${cpId} (${cpLoc}) — TELEMETRY & STEERING ACTIVE</span>
                </div>
                <span class="text-[10px] bg-industrial-green/10 border border-industrial-green/30 px-2 py-0.5 rounded-sm text-industrial-green">ESP-NOW LINK OK</span>
            `;
        }

        if (cpLinkText) {
            const linkHtml = `<span class="text-industrial-green font-bold">CHECKPOINT ${cpId} (${cpLoc})</span>`;
            if (cpLinkText.innerHTML !== linkHtml) cpLinkText.innerHTML = linkHtml;
        }

        if (!lastRoverOnline) {
            playAlertBeep('CONNECT');
        }
        lastRoverOnline = true;

    } else {
        if (banner && (lastRoverOnline || banner.dataset.status !== 'offline')) {
            banner.dataset.status = 'offline';
            banner.className = 'bg-surface-container-low border-2 border-steel-gray text-on-surface-variant px-4 py-2.5 rounded-sm text-xs flex items-center justify-between transition-all';
            banner.innerHTML = `
                <div class="flex items-center gap-2">
                    <span class="material-symbols-outlined text-safety-orange text-sm animate-spin">sync</span>
                    <span>WAITING FOR ROVER TELEMETRY / CHECKPOINT HEARTBEAT...</span>
                </div>
                <span class="text-[10px] bg-carbon-black border border-steel-gray px-2 py-0.5 rounded-sm text-on-surface-variant">STANDBY</span>
            `;
        }

        if (cpLinkText) {
            const linkHtml = `<span class="text-on-surface-variant">OFFLINE / SEARCHING</span>`;
            if (cpLinkText.innerHTML !== linkHtml) cpLinkText.innerHTML = linkHtml;
        }
        lastRoverOnline = false;
    }
}

function renderTopology(cps, bands) {
    const topo = document.getElementById('mesh-topology-view');
    if (!topo) return;

    const currentSig = `${cps ? cps.length : 0}-${bands ? bands.length : 0}`;
    if (currentSig === lastTopologySig) return;
    lastTopologySig = currentSig;

    topo.innerHTML = `
        <div class="bg-carbon-black border border-steel-gray p-3.5 rounded-sm space-y-1">
            <span class="material-symbols-outlined text-industrial-green text-2xl mb-1">engineering</span>
            <div class="font-bold text-on-surface">Worker Bands (${bands ? bands.length : 0})</div>
            <div class="text-[10px] text-on-surface-variant">ESP-NOW 2.4 GHz Broadcast</div>
        </div>
        <div class="bg-carbon-black border border-steel-gray p-3.5 rounded-sm space-y-1">
            <span class="material-symbols-outlined text-hazard-yellow text-2xl mb-1">cell_tower</span>
            <div class="font-bold text-on-surface">Nearest Checkpoint (${cps ? cps.length : 0})</div>
            <div class="text-[10px] text-on-surface-variant">Direct Sensor Ingestion</div>
        </div>
        <div class="bg-carbon-black border border-steel-gray p-3.5 rounded-sm space-y-1">
            <span class="material-symbols-outlined text-safety-orange text-2xl mb-1">alt_route</span>
            <div class="font-bold text-on-surface">Mesh Hop Relays</div>
            <div class="text-[10px] text-on-surface-variant">Subterranean Failover Forwarding</div>
        </div>
        <div class="bg-carbon-black border-2 border-hazard-yellow p-3.5 rounded-sm space-y-1 bg-hazard-yellow/5">
            <span class="material-symbols-outlined text-hazard-yellow text-2xl mb-1">dns</span>
            <div class="font-bold text-hazard-yellow">Surface Station Gateway</div>
            <div class="text-[10px] text-on-surface-variant">Wi-Fi HTTP REST / UDP Control</div>
        </div>
    `;
}

// ================= ROVER CONTROL =================

const speedSlider = document.getElementById('rover-speed-slider');
if (speedSlider) {
    speedSlider.addEventListener('input', (e) => {
        document.getElementById('rover-speed-val').innerText = `${e.target.value} / 255`;
    });
}

async function sendRoverCmd(cmd) {
    const slider = document.getElementById('rover-speed-slider');
    const speed = slider ? parseInt(slider.value) : 200;
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
        if (e.target.tagName !== 'INPUT' && e.target.tagName !== 'TEXTAREA') {
            e.preventDefault();
        }
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

    // Only refresh charts that are actually in the currently active/visible view
    if (currentView === 'charts') {
        const gasChart = document.getElementById('chart-gas');
        const vitalsChart = document.getElementById('chart-vitals');
        const noiseChart = document.getElementById('chart-noise');
        const envChart = document.getElementById('chart-environment');

        if (gasChart) gasChart.src = `/api/charts/gas.png?t=${ts}`;
        if (vitalsChart) vitalsChart.src = `/api/charts/vitals.png?t=${ts}`;
        if (noiseChart) noiseChart.src = `/api/charts/noise.png?t=${ts}`;
        if (envChart) envChart.src = `/api/charts/environment.png?t=${ts}`;
    } else if (currentView === 'workers') {
        const vitalsDedicated = document.getElementById('chart-vitals-dedicated');
        if (vitalsDedicated) vitalsDedicated.src = `/api/charts/vitals.png?t=${ts}`;
    } else if (currentView === 'checkpoints') {
        const gasDedicated = document.getElementById('chart-gas-dedicated');
        if (gasDedicated) gasDedicated.src = `/api/charts/gas.png?t=${ts}`;
    }
    // On 'overview', 'incidents', 'rover', 'topology', NO charts are displayed; do 0 requests!
}

function refreshCamera() {
    if (isCameraLoading) return; // Prevent piling up concurrent image requests
    const ts = Date.now();

    let activeCamEl = null;
    if (currentView === 'overview') {
        activeCamEl = document.getElementById('overview-rover-camera-img');
    } else if (currentView === 'rover') {
        activeCamEl = document.getElementById('rover-camera-img');
    }

    if (activeCamEl) {
        isCameraLoading = true;
        const tempImg = new Image();
        tempImg.onload = () => {
            activeCamEl.src = tempImg.src;
            isCameraLoading = false;
        };
        tempImg.onerror = () => {
            isCameraLoading = false;
        };
        tempImg.src = `/api/camera/latest.jpg?t=${ts}`;
    }
}

// ================= ALERTS ACK & THRESHOLDS =================

async function clearAlerts() {
    await fetch('/api/alerts/ack', {method: 'POST'});
    fetchSystemData();
}

function openThresholdModal() {
    const spo2 = document.getElementById('th-spo2-critical');
    const mq5 = document.getElementById('th-mq5-critical');
    const mq7 = document.getElementById('th-mq7-critical');
    const mq135 = document.getElementById('th-mq135-critical');
    const sound = document.getElementById('th-sound-spike');

    if (spo2) spo2.value = currentThresholds.spo2_critical || 90.0;
    if (mq5) mq5.value = currentThresholds.mq5_critical || 600;
    if (mq7) mq7.value = currentThresholds.mq7_critical || 450;
    if (mq135) mq135.value = currentThresholds.mq135_critical || 550;
    if (sound) sound.value = currentThresholds.sound_spike_threshold || 350;

    const modal = document.getElementById('threshold-modal');
    if (modal) {
        modal.classList.remove('hidden');
        modal.classList.add('flex');
    }
}

function closeThresholdModal() {
    const modal = document.getElementById('threshold-modal');
    if (modal) {
        modal.classList.add('hidden');
        modal.classList.remove('flex');
    }
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

// ================= INITIALIZATION =================

window.addEventListener('DOMContentLoaded', () => {
    // 1. Determine Initial View from body attribute or URL pathname
    const bodyInitialView = document.body.dataset.initialView;
    const path = window.location.pathname;
    const mappedView = PATH_TO_VIEW[path] || bodyInitialView || 'overview';
    switchView(mappedView);

    // 2. Fetch initial data and start timers
    fetchSystemData();
    refreshCharts();

    setInterval(fetchSystemData, 1000);
    setInterval(refreshCharts, 2000);
    setInterval(refreshCamera, 300);
});
