/**
 * WebSocket client and event router.
 *
 * Connects to the visualization server and routes incoming events
 * to registered handlers by event_type. Handles reconnection,
 * per-connection sequence tracking, gap detection, and graph subscriptions.
 */
/**
 * Create a WebSocket connection and event router.
 *
 * Automatically reconnects on disconnect with exponential backoff.
 * Returns an EventRouter for subscribing to specific event types.
 */
export const createEventRouter = (wsUrl, onStatusChange) => {
    const subscriptions = new Map();
    let nextId = 0;
    let ws = null;
    let reconnectDelay = 1000;
    let reconnectTimer = null;
    // Sequence tracking
    let lastSeq = 0;
    let gapCallback = null;
    // Graph subscription state (sent to server after connect)
    let pendingGraphSubscription = null;
    const dispatch = (event) => {
        for (const sub of subscriptions.values()) {
            if (sub.eventType === null || sub.eventType === event.event_type) {
                try {
                    sub.handler(event);
                }
                catch (err) {
                    console.error(`Event handler error for ${event.event_type}:`, err);
                }
            }
        }
    };
    const sendSubscription = () => {
        if (ws && ws.readyState === WebSocket.OPEN && pendingGraphSubscription !== undefined) {
            ws.send(JSON.stringify({ subscribe: pendingGraphSubscription }));
        }
    };
    const connect = () => {
        if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) {
            return;
        }
        ws = new WebSocket(wsUrl);
        ws.onopen = () => {
            reconnectDelay = 1000;
            lastSeq = 0; // Reset sequence on new connection
            onStatusChange(true);
            sendSubscription();
        };
        ws.onmessage = (msg) => {
            try {
                const wire = JSON.parse(msg.data);
                // Sequence gap detection
                if (wire.seq !== undefined) {
                    if (lastSeq > 0 && wire.seq !== lastSeq + 1) {
                        gapCallback?.();
                    }
                    lastSeq = wire.seq;
                }
                dispatch(wire);
            }
            catch (err) {
                console.error("Failed to parse event:", err);
            }
        };
        ws.onclose = () => {
            onStatusChange(false);
            scheduleReconnect();
        };
        ws.onerror = () => {
            ws?.close();
        };
    };
    const scheduleReconnect = () => {
        if (reconnectTimer)
            return;
        reconnectTimer = setTimeout(() => {
            reconnectTimer = null;
            reconnectDelay = Math.min(reconnectDelay * 1.5, 10000);
            connect();
        }, reconnectDelay);
    };
    const subscribe = (eventType, handler) => {
        const id = nextId++;
        subscriptions.set(id, { id, eventType, handler });
        return () => { subscriptions.delete(id); };
    };
    const subscribeAll = (handler) => {
        const id = nextId++;
        subscriptions.set(id, { id, eventType: null, handler });
        return () => { subscriptions.delete(id); };
    };
    const setGraphSubscription = (graphs) => {
        pendingGraphSubscription = graphs;
        sendSubscription();
    };
    const onGapDetected = (callback) => {
        gapCallback = callback;
    };
    // Start connection
    connect();
    return { subscribe, subscribeAll, setGraphSubscription, onGapDetected };
};
