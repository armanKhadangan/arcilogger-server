import {
  addRecipients,
  addressProblem,
  RECIPIENTS_MAX,
  removeRecipient,
  sameAddress,
  splitAddresses,
} from './recipients-model';

describe('recipient addresses', () => {
  it('splits typed or pasted lists on commas, semicolons, spaces and lines', () => {
    expect(splitAddresses(' a@x.com.tr, b@x.com.tr;c@x.com.tr\n<d@x.com.tr>  ')).toEqual([
      'a@x.com.tr',
      'b@x.com.tr',
      'c@x.com.tr',
      'd@x.com.tr',
    ]);
    expect(splitAddresses(' ,; ')).toEqual([]);
  });

  it('checks one address', () => {
    expect(addressProblem('muhasebe@ornek.com.tr', [])).toBeUndefined();
    expect(addressProblem('muhasebe@', [])).toBe('validation.email');
    expect(addressProblem('mu‮hasebe@ornek.com.tr', [])).toBe('validation.email');
    expect(addressProblem(`${'x'.repeat(250)}@a.co`, [])).toBe('validation.tooLong');
    expect(addressProblem('Tolga@Ornek.com.tr', ['tolga@ornek.com.tr'])).toBe(
      'reports.recipients.duplicate',
    );
    expect(sameAddress('A@B.TR', 'a@b.tr')).toBe(true);
  });

  it('adds the valid ones and leaves the rest in the field with the first problem', () => {
    expect(addRecipients(['a@x.com.tr'], 'b@x.com.tr, yanlis@, c@x.com.tr, a@x.com.tr')).toEqual({
      recipients: ['a@x.com.tr', 'b@x.com.tr', 'c@x.com.tr'],
      rest: 'yanlis@',
      problem: 'validation.email',
    });
    // One typed address that is already there is explained, not dropped silently.
    expect(addRecipients(['a@x.com.tr'], 'A@x.com.tr')).toEqual({
      recipients: ['a@x.com.tr'],
      rest: 'A@x.com.tr',
      problem: 'reports.recipients.duplicate',
    });
    expect(addRecipients([], '')).toEqual({ recipients: [], rest: '' });
  });

  it(`stops at ${RECIPIENTS_MAX} recipients`, () => {
    const full = Array.from({ length: RECIPIENTS_MAX }, (_, index) => `k${index}@x.com.tr`);
    expect(addRecipients(full, 'yeni@x.com.tr')).toEqual({
      recipients: full,
      rest: 'yeni@x.com.tr',
      problem: 'reports.recipients.tooMany',
    });
  });

  it('removes one chip', () => {
    expect(removeRecipient(['a', 'b', 'c'], 1)).toEqual(['a', 'c']);
  });
});
