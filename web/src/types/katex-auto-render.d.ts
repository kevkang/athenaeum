// katex ships this module with no bundled TypeScript declarations (checked:
// only katex/types/katex.d.ts exists, nothing under contrib/).
declare module 'katex/contrib/auto-render' {
  interface AutoRenderDelimiter {
    left: string;
    right: string;
    display: boolean;
  }

  interface AutoRenderOptions {
    delimiters?: AutoRenderDelimiter[];
    throwOnError?: boolean;
  }

  export default function renderMathInElement(
    element: HTMLElement,
    options?: AutoRenderOptions,
  ): void;
}
