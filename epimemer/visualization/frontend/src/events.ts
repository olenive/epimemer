/**
 * WebSocket client and event router.
 *
 * Connects to the visualization server and routes incoming events
 * to registered handlers by event_type. Handles reconnection.
 */

import type { AnyEvent } from "./types";

export type EventHandler = (event: AnyEvent) => void;

export interface EventRouter {
  subscribe: (eventType: string, handler: EventHandler) => () => void;
  subscribeAll: (handler: EventHandler) => () => void;
}

interface Subscription {
  id: number;
  eventType: string | null; // null = all events
  handler: EventHandler;
}

/**
 * Create a WebSocket connection and event router.
 *
 * Automatically reconnects on disconnect with exponential backoff.
 * Returns an EventRouter for subscribing to specific event types.
 */
export const createEventRouter = (
  wsUrl: string,
  onStatusChange: (connected: boolean) => void,
): EventRouter => {
  const subscriptions = new Map<number, Subscription>();
  let nextId = 0;
  let ws: WebSocket | null = null;
  let reconnectDelay = 1000;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  const dispatch = (event: AnyEvent): void => {
    for (const sub of subscriptions.values()) {
      if (sub.eventType === null || sub.eventType === event.event_type) {
        try {
          sub.handler(event);
        } catch (err) {
          console.error(`Event handler error for ${event.event_type}:`, err);
        }
      }
    }
  };

  const connect = (): void => {
    if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) {
      return;
    }

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      reconnectDelay = 1000;
      onStatusChange(true);
    };

    ws.onmessage = (msg) => {
      try {
        const event = JSON.parse(msg.data) as AnyEvent;
        dispatch(event);
      } catch (err) {
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

  const scheduleReconnect = (): void => {
    if (reconnectTimer) return;
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      reconnectDelay = Math.min(reconnectDelay * 1.5, 10000);
      connect();
    }, reconnectDelay);
  };

  const subscribe = (eventType: string, handler: EventHandler): (() => void) => {
    const id = nextId++;
    subscriptions.set(id, { id, eventType, handler });
    return () => { subscriptions.delete(id); };
  };

  const subscribeAll = (handler: EventHandler): (() => void) => {
    const id = nextId++;
    subscriptions.set(id, { id, eventType: null, handler });
    return () => { subscriptions.delete(id); };
  };

  // Start connection
  connect();

  return { subscribe, subscribeAll };
};
