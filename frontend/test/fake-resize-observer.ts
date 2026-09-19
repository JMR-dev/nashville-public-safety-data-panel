// jsdom has no ResizeObserver. This stand-in records observed elements so tests can report
// size changes the way a browser would after layout.
export class FakeResizeObserver {
  static readonly instances: FakeResizeObserver[] = [];

  static resize(element: Element): void {
    for (const observer of this.instances) {
      if (observer.targets.has(element)) {
        observer.callback([], observer as unknown as ResizeObserver);
      }
    }
  }

  private readonly callback: ResizeObserverCallback;
  private readonly targets = new Set<Element>();

  constructor(callback: ResizeObserverCallback) {
    this.callback = callback;
    FakeResizeObserver.instances.push(this);
  }

  observe(element: Element): void {
    this.targets.add(element);
  }

  disconnect(): void {
    this.targets.clear();
  }
}
