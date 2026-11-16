import { describe, expect, it } from 'vitest';

import { clockOffsetText } from './clock-offset';
import i18n from '@/lib/i18n';

describe('clockOffsetText', () => {
  it('says minutes behind or ahead, and on time under a minute', async () => {
    await i18n.changeLanguage('tr');
    const t = i18n.t.bind(i18n);
    expect(clockOffsetText(0, t)).toBe('zamanında');
    expect(clockOffsetText(-420, t)).toBe('7 dk geri');
    expect(clockOffsetText(10_800, t)).toBe('180 dk ileri');
    expect(clockOffsetText(-61, t)).toBe('1 dk geri');
    await i18n.changeLanguage('en');
    expect(clockOffsetText(-420, i18n.t.bind(i18n))).toBe('7 min behind');
    expect(clockOffsetText(90, i18n.t.bind(i18n))).toBe('2 min ahead');
  });
});
