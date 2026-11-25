// Django's JavaScript catalog (`{% url 'javascript-catalog' %}`) defines these globals when the
// page loads it. Without it, text stays in English.
declare global {
  interface Window {
    gettext?: (text: string) => string;
    interpolate?: (format: string, args: string[]) => string;
  }
}

export function gettext(text: string): string {
  return window.gettext ? window.gettext(text) : text;
}

export function interpolate(format: string, args: string[]): string {
  if (window.interpolate) return window.interpolate(format, args);
  // A function, so "$&" and similar in an argument are not read as replacement patterns.
  return args.reduce((text, arg) => text.replace('%s', () => arg), format);
}
