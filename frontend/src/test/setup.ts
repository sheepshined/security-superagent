import "@testing-library/jest-dom/vitest";

Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => undefined,
    removeListener: () => undefined,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => false
  })
});

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

Object.defineProperty(window, "ResizeObserver", {value: ResizeObserverStub});
Object.defineProperty(globalThis, "ResizeObserver", {value: ResizeObserverStub});

const nativeGetComputedStyle = window.getComputedStyle.bind(window);
window.getComputedStyle = (element: Element) => nativeGetComputedStyle(element);
