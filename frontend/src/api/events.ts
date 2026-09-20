import { useEffect, useEffectEvent, useState } from "react";

// Live data-version notifications from /events. The browser's EventSource reconnects by itself
// after network interruptions, sending the last event id; the server then waits for the next
// change, so a reconnect is also a cue to refresh. If the server ends the stream (for example
// while the API restarts), EventSource gives up, so a new one is opened after a delay.

const CLOSED = 2;
const REOPEN_DELAY_MS = 5000;

export type Connection = "connecting" | "open" | "reconnecting";

export interface VersionSource {
  readonly readyState: number;
  addEventListener(type: string, listener: (event: MessageEvent<string>) => void): void;
  close(): void;
}

export type OpenVersionSource = (url: string) => VersionSource;

export interface VersionHandlers {
  onVersion: (version: number) => void;
  onReconnect: () => void;
}

export function useDataVersion(open: OpenVersionSource, handlers: VersionHandlers): Connection {
  const [connection, setConnection] = useState<Connection>("connecting");
  const [attempt, setAttempt] = useState(0);
  const onVersion = useEffectEvent(handlers.onVersion);
  const onReconnect = useEffectEvent(handlers.onReconnect);

  useEffect(() => {
    const source = open("/events");
    let wasInterrupted = attempt > 0;
    let reopen: ReturnType<typeof setTimeout> | undefined;
    source.addEventListener("open", () => {
      setConnection("open");
      if (!wasInterrupted) {
        return;
      }
      wasInterrupted = false;
      onReconnect();
    });
    source.addEventListener("version", (event) => {
      const { version } = JSON.parse(event.data) as { version: number };
      onVersion(version);
    });
    source.addEventListener("error", () => {
      wasInterrupted = true;
      setConnection("reconnecting");
      if (source.readyState === CLOSED) {
        reopen = setTimeout(() => {
          setAttempt((count) => count + 1);
        }, REOPEN_DELAY_MS);
      }
    });
    return () => {
      clearTimeout(reopen);
      source.close();
    };
  }, [open, attempt]);

  return connection;
}
