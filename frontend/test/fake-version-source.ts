import type { OpenVersionSource, VersionSource } from "../src/api/events.ts";

type Listener = (event: MessageEvent<string>) => void;

// A controllable stand-in for the browser's EventSource, which jsdom does not provide.
export class FakeVersionSource implements VersionSource {
  static readonly instances: FakeVersionSource[] = [];

  static latest(): FakeVersionSource {
    const source = this.instances.at(-1);
    if (!source) {
      throw new Error("No event source has been opened");
    }
    return source;
  }

  static reset(): void {
    this.instances.length = 0;
  }

  private readonly listeners = new Map<string, Listener[]>();
  readyState = 0;
  readonly url: string;

  constructor(url: string) {
    this.url = url;
    FakeVersionSource.instances.push(this);
  }

  private dispatch(type: string, data: string): void {
    const listeners = this.listeners.get(type) ?? [];
    for (const listener of listeners) {
      listener(new MessageEvent(type, { data }));
    }
  }

  addEventListener(type: string, listener: Listener): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  close(): void {
    this.readyState = 2;
  }

  open(): void {
    this.readyState = 1;
    this.dispatch("open", "");
  }

  version(version: number): void {
    this.dispatch("version", JSON.stringify({ version }));
  }

  fail({ fatal = false } = {}): void {
    this.readyState = fatal ? 2 : 0;
    this.dispatch("error", "");
  }
}

export const openFakeSource: OpenVersionSource = (url) => new FakeVersionSource(url);
