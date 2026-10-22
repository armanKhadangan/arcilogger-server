import { clsx, type ClassValue } from 'clsx';
import { extendTailwindMerge } from 'tailwind-merge';

// `text-13` is a font size from our theme; without this, tailwind-merge would treat it as a text
// colour and silently drop e.g. `text-primary-foreground` from small buttons.
const twMerge = extendTailwindMerge({ extend: { theme: { text: ['13'] } } });

/** Merge Tailwind class names, letting later classes win over earlier conflicting ones. */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

function firstCharacter(word: string): string {
  const code = word.codePointAt(0);
  return code === undefined ? '' : String.fromCodePoint(code);
}

/** Initials for an avatar ("tolga yılmaz" → "TY", "ismail" → "İ"), upper-cased the Turkish way. */
export function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  const first = words[0];
  if (first === undefined) return '';
  const last = words.length > 1 ? words[words.length - 1] : undefined;
  return `${firstCharacter(first)}${last ? firstCharacter(last) : ''}`.toLocaleUpperCase('tr-TR');
}
