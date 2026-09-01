/**
 * Field Safety Monitoring System - Universal JavaScript/TypeScript Client SDK
 * Compatible with React, Vue, Svelte, Angular, React Native, Electron, and Vanilla JS.
 */

export class FieldSafetyClient {
    /**
     * @param {Object} options
     * @param {string} options.baseUrl - HTTP API base URL (e.g. "http://localhost:8000")
     * @param {string} [options.wsUrl] - WebSocket URL (defaults to auto-detected ws/wss)
     */
    constructor(options = {}) {
        this.baseUrl = (options.baseUrl || "http://localhost:8000").replace(/\/$/, "");
        this.wsUrl = options.wsUrl || this.baseUrl.replace(/^http/, "ws") + "/api/v1/ws/live";
        this.ws = null;
        this.telemetryListeners = [];
        this.alertListeners = [];
    }

    // ================= REST API QUERIES =================

    /** Get full system snapshot */
    async getFullSnapshot() {
        const res = await fetch(`${this.baseUrl}/api/v1/data`);
        if (!res.ok) throw new Error(`HTTP Error: ${res.status}`);
        return await res.json();
    }

    /** Get system health summary */
    async getSystemSummary() {
        const res = await fetch(`${this.baseUrl}/api/v1/system/summary`);
        return await res.json();
    }

    /** Get all checkpoints & sensor readings */
    async getCheckpoints() {
        const res = await fetch(`${this.baseUrl}/api/v1/sensors/checkpoints`);
        return await res.json();
    }

    /** Get specific checkpoint details and rolling history */
    async getCheckpoint(id) {
        const res = await fetch(`${this.baseUrl}/api/v1/sensors/checkpoints/${id}`);
        return await res.json();
    }

    /** Get all worker bands & vitals */
    async getWorkerBands() {
        const res = await fetch(`${this.baseUrl}/api/v1/sensors/bands`);
        return await res.json();
    }

    /** Get live camera snapshot (Base64 Data URI) */
    async getCameraSnapshot() {
        const res = await fetch(`${this.baseUrl}/api/v1/camera/snapshot`);
        return await res.json();
    }

    /** Get MJPEG video stream URL (can be passed to <img src="...">) */
    getCameraStreamUrl() {
        return `${this.baseUrl}/api/v1/camera/stream`;
    }

    /** Send steering command to rover */
    async sendRoverCommand(command, speed = 200, roverId = 1) {
        const res = await fetch(`${this.baseUrl}/api/v1/rover/command`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ command, speed, rover_id: roverId })
        });
        return await res.json();
    }

    /** Acknowledge active safety alerts */
    async acknowledgeAlerts(alertId = null) {
        const url = alertId 
            ? `${this.baseUrl}/api/v1/alerts/ack?alert_id=${alertId}`
            : `${this.baseUrl}/api/v1/alerts/ack`;
        const res = await fetch(url, { method: "POST" });
        return await res.json();
    }

    // ================= REAL-TIME WEBSOCKET SUBSCRIPTION =================

    /** Connect to real-time WebSocket */
    connectWebSocket(onConnected = null, onDisconnected = null) {
        this.ws = new WebSocket(this.wsUrl);

        this.ws.onopen = () => {
            if (onConnected) onConnected();
        };

        this.ws.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                if (msg.type === "initial_state" || msg.type === "telemetry_update") {
                    this.telemetryListeners.forEach(cb => cb(msg.data));
                } else if (msg.type === "alert") {
                    this.alertListeners.forEach(cb => cb(msg.data));
                }
            } catch (err) {
                console.error("[FieldSafetyClient] Failed to parse WebSocket packet:", err);
            }
        };

        this.ws.onclose = () => {
            if (onDisconnected) onDisconnected();
        };

        this.ws.onerror = (err) => {
            console.error("[FieldSafetyClient] WebSocket error:", err);
        };
    }

    /** Subscribe to real-time telemetry updates */
    onTelemetry(callback) {
        this.telemetryListeners.push(callback);
    }

    /** Subscribe to real-time alerts */
    onAlert(callback) {
        this.alertListeners.push(callback);
    }

    /** Send Rover command via active WebSocket for lowest latency */
    sendRoverCommandWs(command, speed = 200) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ action: "rover_command", command, speed }));
        } else {
            // Fallback to REST POST
            this.sendRoverCommand(command, speed);
        }
    }

    /** Disconnect WebSocket */
    disconnect() {
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
    }
}
