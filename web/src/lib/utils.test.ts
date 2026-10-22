import { cn } from './utils';

describe('cn', () => {
  it('keeps colour and the custom 13 px size together', () => {
    expect(cn('bg-primary text-primary-foreground', 'h-8 text-13')).toBe(
      'bg-primary text-primary-foreground h-8 text-13',
    );
  });

  it('lets later font sizes and colours win', () => {
    expect(cn('text-sm', 'text-13')).toBe('text-13');
    expect(cn('text-muted', 'text-foreground')).toBe('text-foreground');
  });
});
